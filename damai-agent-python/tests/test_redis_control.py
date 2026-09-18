from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import AsyncMock

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from damai_agent.redis_control import RedisTurnCancellationStore


class CancellationContractTest(unittest.IsolatedAsyncioTestCase):
    def test_key_is_tenant_session_turn_isolated_and_private(self) -> None:
        store = RedisTurnCancellationStore(AsyncMock(spec=Redis))
        key = store.key_for("secret-tenant", "secret-session", "secret-turn")
        self.assertNotIn("secret-tenant", key)
        self.assertNotIn("secret-session", key)
        self.assertNotIn("secret-turn", key)
        self.assertNotEqual(key, store.key_for("other", "secret-session", "secret-turn"))
        self.assertNotEqual(key, store.key_for("secret-tenant", "other", "secret-turn"))
        self.assertNotEqual(key, store.key_for("secret-tenant", "secret-session", "other"))
        with self.assertRaises(ValueError):
            store.key_for("", "session", "turn")
        with self.assertRaises(ValueError):
            RedisTurnCancellationStore(AsyncMock(spec=Redis), ttl_ms=1)

    async def test_redis_error_fails_closed(self) -> None:
        client = AsyncMock(spec=Redis)
        store = RedisTurnCancellationStore(client)
        client.exists.side_effect = RedisConnectionError("unavailable")
        with self.assertRaises(RedisConnectionError):
            await store.is_requested("tenant", "session", "turn")


@unittest.skipUnless(os.environ.get("DAMAI_TEST_REDIS_HOST"), "test Redis host not set")
class CancellationIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = Redis(
            host=os.environ["DAMAI_TEST_REDIS_HOST"],
            port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
            username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
            password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
            decode_responses=True,
        )
        self.store = RedisTurnCancellationStore(
            self.client, key_prefix=f"damai:agent:cancel-test:{uuid.uuid4().hex}:"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_signal_is_isolated_idempotent_and_clearable(self) -> None:
        self.assertFalse(await self.store.is_requested("tenant", "session", "turn"))
        await self.store.request("tenant", "session", "turn")
        await self.store.request("tenant", "session", "turn")
        self.assertTrue(await self.store.is_requested("tenant", "session", "turn"))
        self.assertFalse(await self.store.is_requested("other", "session", "turn"))
        self.assertGreater(
            await self.client.pttl(self.store.key_for("tenant", "session", "turn")), 0
        )
        await self.store.clear("tenant", "session", "turn")
        self.assertFalse(await self.store.is_requested("tenant", "session", "turn"))
