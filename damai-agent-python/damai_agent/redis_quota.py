"""Atomic, tenant-isolated daily estimated-cost accounting in Redis."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from redis.asyncio import Redis

_CHARGE_SCRIPT = """
local old = redis.call('GET', KEYS[2])
if old then
  if tonumber(old) ~= tonumber(ARGV[1]) then return redis.error_reply('CHARGE_CONFLICT') end
  local used = tonumber(redis.call('GET', KEYS[1]) or '0')
  return {used <= tonumber(ARGV[2]) and 1 or 0, used}
end
local used = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[4])
return {used <= tonumber(ARGV[2]) and 1 or 0, used}
"""


class RedisDailyQuota:
    def __init__(self, client: Redis, *, key_prefix: str = "damai:agent:v1:quota:") -> None:
        if not key_prefix or any(char in key_prefix for char in "\r\n\0{}"):
            raise ValueError("invalid Redis quota key prefix")
        self._client = client
        self._key_prefix = key_prefix

    def keys_for(self, tenant_id: str, turn_id: str, round_number: int) -> tuple[str, str]:
        if not tenant_id or not turn_id or round_number < 1:
            raise ValueError("tenant, turn, and model round are required")
        tenant_digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        charge_digest = hashlib.sha256(f"{turn_id}\0{round_number}".encode("utf-8")).hexdigest()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        tag = f"{{{tenant_digest}}}"
        return (
            f"{self._key_prefix}{tag}:day:{day}",
            f"{self._key_prefix}{tag}:charge:{charge_digest}",
        )

    async def exhausted(self, tenant_id: str, limit_micro_usd: int) -> bool:
        if limit_micro_usd <= 0:
            raise ValueError("daily quota must be positive")
        daily_key, _ = self.keys_for(tenant_id, "preflight", 1)
        used = await self._client.get(daily_key)
        return int(used or 0) >= limit_micro_usd

    async def charge(
        self,
        tenant_id: str,
        turn_id: str,
        round_number: int,
        amount_micro_usd: int,
        limit_micro_usd: int,
    ) -> bool:
        if not 0 <= amount_micro_usd <= 1_000_000_000_000_000 or limit_micro_usd <= 0:
            raise ValueError("invalid quota amount or limit")
        daily_key, charge_key = self.keys_for(tenant_id, turn_id, round_number)
        response = await self._client.eval(
            _CHARGE_SCRIPT,
            2,
            daily_key,
            charge_key,
            amount_micro_usd,
            limit_micro_usd,
            172800,
            691200,
        )
        return int(response[0]) == 1
