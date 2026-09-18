"""Short-lived, tenant-isolated cancellation signals for durable Turns."""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis


class RedisTurnCancellationStore:
    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "damai:agent:v1:turn-cancel:",
        ttl_ms: int = 3_600_000,
    ) -> None:
        if not key_prefix or any(char in key_prefix for char in "\r\n\0"):
            raise ValueError("invalid Redis cancellation key prefix")
        if not 1_000 <= ttl_ms <= 86_400_000:
            raise ValueError("invalid cancellation TTL")
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_ms = ttl_ms

    def key_for(self, tenant_id: str, session_key: str, turn_id: str) -> str:
        if not tenant_id or not session_key or not turn_id:
            raise ValueError("tenant, session, and turn are required")
        digest = hashlib.sha256(
            f"{tenant_id}\0{session_key}\0{turn_id}".encode("utf-8")
        ).hexdigest()
        return f"{self._key_prefix}{digest}"

    async def request(self, tenant_id: str, session_key: str, turn_id: str) -> None:
        await self._client.set(self.key_for(tenant_id, session_key, turn_id), "1", px=self._ttl_ms)

    async def is_requested(self, tenant_id: str, session_key: str, turn_id: str) -> bool:
        return bool(await self._client.exists(self.key_for(tenant_id, session_key, turn_id)))

    async def clear(self, tenant_id: str, session_key: str, turn_id: str) -> None:
        await self._client.delete(self.key_for(tenant_id, session_key, turn_id))
