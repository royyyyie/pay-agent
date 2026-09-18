"""Single-Redis owner-token leases for cross-process session coordination."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True, slots=True)
class SessionLease:
    key: str
    owner_token: str
    ttl_ms: int


class RedisSessionLeaseStore:
    """Lease primitive; caller must stop work if renewal fails or raises.

    A lease alone is not a fencing token. Durable writes must still reject stale
    owners; do not treat this class as complete multi-replica turn serialization.
    """

    def __init__(self, client: Redis, key_prefix: str = "damai:agent:v1:session-lease:") -> None:
        if not key_prefix or any(char in key_prefix for char in "\r\n\0"):
            raise ValueError("invalid Redis lease key prefix")
        self._client = client
        self._key_prefix = key_prefix

    def key_for(self, tenant_id: str, session_key: str) -> str:
        if not tenant_id or not session_key:
            raise ValueError("tenant and session identity are required")
        digest = hashlib.sha256(f"{tenant_id}\0{session_key}".encode("utf-8")).hexdigest()
        return f"{self._key_prefix}{digest}"

    async def acquire(
        self, tenant_id: str, session_key: str, *, ttl_ms: int = 30_000
    ) -> SessionLease | None:
        if not 100 <= ttl_ms <= 300_000:
            raise ValueError("lease TTL must be between 100 and 300000 ms")
        key = self.key_for(tenant_id, session_key)
        token = secrets.token_hex(24)
        acquired = await self._client.set(key, token, nx=True, px=ttl_ms)
        if not acquired:
            return None
        return SessionLease(key=key, owner_token=token, ttl_ms=ttl_ms)

    async def renew(self, lease: SessionLease) -> bool:
        return bool(
            await self._client.eval(_RENEW_SCRIPT, 1, lease.key, lease.owner_token, lease.ttl_ms)
        )

    async def release(self, lease: SessionLease) -> bool:
        return bool(await self._client.eval(_RELEASE_SCRIPT, 1, lease.key, lease.owner_token))
