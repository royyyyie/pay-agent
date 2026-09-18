from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import AsyncMock

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from damai_agent.redis_lease import RedisSessionLeaseStore, SessionLease


class RedisLeaseContractTest(unittest.IsolatedAsyncioTestCase):
    def test_key_hides_tenant_and_session(self) -> None:
        store = RedisSessionLeaseStore(AsyncMock(spec=Redis))
        key = store.key_for("private-tenant", "private-session")
        self.assertNotIn("private-tenant", key)
        self.assertNotIn("private-session", key)
        self.assertNotEqual(key, store.key_for("other-tenant", "private-session"))
        with self.assertRaises(ValueError):
            store.key_for("", "private-session")

    async def test_invalid_ttl_and_redis_failure_fail_closed(self) -> None:
        client = AsyncMock(spec=Redis)
        store = RedisSessionLeaseStore(client)
        with self.assertRaises(ValueError):
            await store.acquire("tenant", "session", ttl_ms=0)
        client.set.side_effect = RedisConnectionError("unavailable")
        with self.assertRaises(RedisConnectionError):
            await store.acquire("tenant", "session")
        client.eval.side_effect = RedisConnectionError("unavailable")
        lease = SessionLease(store.key_for("tenant", "session"), "owner", 1000)
        with self.assertRaises(RedisConnectionError):
            await store.renew(lease)
        with self.assertRaises(RedisConnectionError):
            await store.release(lease)


@unittest.skipUnless(os.environ.get("DAMAI_TEST_REDIS_HOST"), "test Redis host not set")
class RedisLeaseIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = Redis(
            host=os.environ["DAMAI_TEST_REDIS_HOST"],
            port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
            username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
            password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
        self.store = RedisSessionLeaseStore(
            self.client, key_prefix=f"damai:agent:acceptance:{uuid.uuid4().hex}:"
        )
        self.session_key = uuid.uuid4().hex

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_competing_owners_and_renewal(self) -> None:
        first, second = await asyncio.gather(
            self.store.acquire("tenant-1", self.session_key, ttl_ms=30_000),
            self.store.acquire("tenant-1", self.session_key, ttl_ms=30_000),
        )
        self.assertEqual(sum(lease is not None for lease in (first, second)), 1)
        owner = first or second
        assert owner is not None
        self.assertTrue(await self.store.renew(owner))
        self.assertGreater(await self.client.pttl(owner.key), 0)
        self.assertTrue(await self.store.release(owner))
        self.assertFalse(await self.store.release(owner))

    async def test_expired_owner_cannot_release_or_renew_new_owner(self) -> None:
        old = await self.store.acquire("tenant-1", self.session_key, ttl_ms=300)
        assert old is not None
        await asyncio.sleep(0.6)
        current = await self.store.acquire("tenant-1", self.session_key, ttl_ms=3000)
        assert current is not None
        self.assertNotEqual(old.owner_token, current.owner_token)
        self.assertFalse(await self.store.renew(old))
        self.assertFalse(await self.store.release(old))
        self.assertTrue(await self.store.renew(current))
        self.assertTrue(await self.store.release(current))

    async def test_tenant_isolation(self) -> None:
        first = await self.store.acquire("tenant-1", self.session_key)
        second = await self.store.acquire("tenant-2", self.session_key)
        assert first is not None and second is not None
        self.assertNotEqual(first.key, second.key)
        self.assertTrue(await self.store.release(first))
        self.assertTrue(await self.store.release(second))
