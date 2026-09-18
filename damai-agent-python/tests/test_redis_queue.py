from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import AsyncMock

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from damai_agent.redis_queue import RedisPendingTurnQueue


class PendingQueueContractTest(unittest.IsolatedAsyncioTestCase):
    def test_keys_and_tokens_hide_request_identity(self) -> None:
        queue = RedisPendingTurnQueue(AsyncMock(spec=Redis))
        key = queue.key_for("private-tenant", "private-session")
        token = queue.token_for("private-idempotency", "private-fingerprint")
        self.assertNotIn("private-tenant", key)
        self.assertNotIn("private-session", key)
        self.assertNotIn("private-idempotency", token)
        self.assertNotEqual(key, queue.key_for("other", "private-session"))
        with self.assertRaises(ValueError):
            queue.key_for("", "session")
        with self.assertRaises(ValueError):
            queue.token_for("", "fingerprint")
        with self.assertRaises(ValueError):
            RedisPendingTurnQueue(AsyncMock(spec=Redis), max_pending=0)

    async def test_invalid_token_and_redis_failure_fail_closed(self) -> None:
        client = AsyncMock(spec=Redis)
        queue = RedisPendingTurnQueue(client)
        with self.assertRaises(ValueError):
            await queue.enqueue("tenant", "session", "raw-private-key")
        client.eval.side_effect = RedisConnectionError("unavailable")
        with self.assertRaises(RedisConnectionError):
            await queue.enqueue("tenant", "session", "0" * 64)


@unittest.skipUnless(os.environ.get("DAMAI_TEST_REDIS_HOST"), "test Redis host not set")
class PendingQueueIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = Redis(
            host=os.environ["DAMAI_TEST_REDIS_HOST"],
            port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
            username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
            password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
            decode_responses=True,
        )
        self.queue = RedisPendingTurnQueue(
            self.client,
            key_prefix=f"damai:agent:queue-test:{uuid.uuid4().hex}:",
            max_pending=2,
            item_ttl_ms=30_000,
        )
        self.session = uuid.uuid4().hex
        self.first = self.queue.token_for("idem-1", "f1")
        self.second = self.queue.token_for("idem-2", "f2")
        self.third = self.queue.token_for("idem-3", "f3")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_fifo_dedup_capacity_and_ack(self) -> None:
        self.assertEqual(await self.queue.enqueue("tenant", self.session, self.first), 1)
        self.assertEqual(await self.queue.enqueue("tenant", self.session, self.first), 1)
        self.assertEqual(await self.queue.enqueue("tenant", self.session, self.second), 2)
        with self.assertRaises(OverflowError):
            await self.queue.enqueue("tenant", self.session, self.third)
        self.assertEqual(await self.queue.head("tenant", self.session), self.first)
        self.assertFalse(await self.queue.acknowledge_head("tenant", self.session, self.second))
        self.assertTrue(await self.queue.acknowledge_head("tenant", self.session, self.first))
        self.assertEqual(await self.queue.head("tenant", self.session), self.second)
        self.assertTrue(await self.queue.remove("tenant", self.session, self.second))
        self.assertIsNone(await self.queue.head("tenant", self.session))

    async def test_expired_head_is_pruned_and_tenant_isolated(self) -> None:
        await self.queue.enqueue("tenant", self.session, self.first)
        await self.queue.enqueue("other", self.session, self.second)
        self.assertEqual(await self.queue.head("other", self.session), self.second)
        # Expire only the first request; do not wait for a wall-clock TTL.
        key = self.queue.key_for("tenant", self.session)
        await self.client.delete(key.removesuffix(":list") + ":item:" + self.first)
        self.assertIsNone(await self.queue.head("tenant", self.session))
        self.assertEqual(await self.queue.enqueue("tenant", self.session, self.third), 1)
