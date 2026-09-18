"""Bounded Redis pending-admission queue; payloads stay with the caller."""

from __future__ import annotations

import hashlib
import re

from redis.asyncio import Redis

_PRUNE = """
local members = redis.call('LRANGE', KEYS[1], 0, -1)
for _, member in ipairs(members) do
    if redis.call('EXISTS', ARGV[1] .. member) == 0 then
        redis.call('LREM', KEYS[1], 1, member)
    end
end
"""

_ENQUEUE = (
    _PRUNE
    + """
local position = redis.call('LPOS', KEYS[1], ARGV[2])
if position then
    redis.call('PEXPIRE', ARGV[1] .. ARGV[2], ARGV[3])
    return position + 1
end
if redis.call('LLEN', KEYS[1]) >= tonumber(ARGV[4]) then
    return -1
end
redis.call('SET', ARGV[1] .. ARGV[2], '1', 'PX', ARGV[3])
redis.call('RPUSH', KEYS[1], ARGV[2])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
return redis.call('LLEN', KEYS[1])
"""
)

_HEAD = _PRUNE + "return redis.call('LINDEX', KEYS[1], 0)"

_ACK = (
    _PRUNE
    + """
if redis.call('LINDEX', KEYS[1], 0) ~= ARGV[2] then
    return 0
end
redis.call('LPOP', KEYS[1])
redis.call('DEL', ARGV[1] .. ARGV[2])
return 1
"""
)

_REMOVE = (
    _PRUNE
    + """
local removed = redis.call('LREM', KEYS[1], 1, ARGV[2])
redis.call('DEL', ARGV[1] .. ARGV[2])
return removed
"""
)


class RedisPendingTurnQueue:
    """FIFO admission hints; callers retain their request and retry with the same key."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "damai:agent:v1:pending:",
        item_ttl_ms: int = 300_000,
        max_pending: int = 32,
    ) -> None:
        if not key_prefix or any(char in key_prefix for char in "\r\n\0"):
            raise ValueError("invalid Redis pending key prefix")
        if not 1_000 <= item_ttl_ms <= 3_600_000:
            raise ValueError("invalid pending item TTL")
        if not 1 <= max_pending <= 100:
            raise ValueError("invalid pending queue capacity")
        self._client = client
        self._key_prefix = key_prefix
        self._item_ttl_ms = item_ttl_ms
        self._max_pending = max_pending

    def key_for(self, tenant_id: str, session_key: str) -> str:
        if not tenant_id or not session_key:
            raise ValueError("tenant and session are required")
        digest = hashlib.sha256(f"{tenant_id}\0{session_key}".encode("utf-8")).hexdigest()
        return f"{self._key_prefix}{{{digest}}}:list"

    @staticmethod
    def token_for(idempotency_key: str, request_fingerprint: str) -> str:
        if not idempotency_key or not request_fingerprint:
            raise ValueError("request identity is required")
        return hashlib.sha256(
            f"{idempotency_key}\0{request_fingerprint}".encode("utf-8")
        ).hexdigest()

    def _keys(self, tenant_id: str, session_key: str) -> tuple[str, str]:
        key = self.key_for(tenant_id, session_key)
        return key, key.removesuffix(":list") + ":item:"

    @staticmethod
    def _validate_token(token: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise ValueError("invalid pending request token")

    async def enqueue(self, tenant_id: str, session_key: str, token: str) -> int:
        self._validate_token(token)
        key, marker_prefix = self._keys(tenant_id, session_key)
        position = int(
            await self._client.eval(
                _ENQUEUE,
                1,
                key,
                marker_prefix,
                token,
                self._item_ttl_ms,
                self._max_pending,
            )
        )
        if position == -1:
            raise OverflowError("pending session queue is full")
        return position

    async def head(self, tenant_id: str, session_key: str) -> str | None:
        key, marker_prefix = self._keys(tenant_id, session_key)
        result = await self._client.eval(_HEAD, 1, key, marker_prefix)
        if result is None:
            return None
        return result.decode("utf-8") if isinstance(result, bytes) else str(result)

    async def acknowledge_head(self, tenant_id: str, session_key: str, token: str) -> bool:
        self._validate_token(token)
        key, marker_prefix = self._keys(tenant_id, session_key)
        return bool(await self._client.eval(_ACK, 1, key, marker_prefix, token))

    async def remove(self, tenant_id: str, session_key: str, token: str) -> bool:
        self._validate_token(token)
        key, marker_prefix = self._keys(tenant_id, session_key)
        return bool(await self._client.eval(_REMOVE, 1, key, marker_prefix, token))
