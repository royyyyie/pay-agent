from __future__ import annotations

import asyncio
import json
import os
import unittest
import urllib.request
import uuid
from typing import Sequence
from unittest.mock import AsyncMock, patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.status import StatusCode
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from damai_agent.config import ModelPrice
from damai_agent.models import (
    AgentErrorCode,
    ChatMessage,
    ProviderResponse,
    ProviderUsage,
    ToolContext,
    ToolSpec,
)
from damai_agent.providers import ProviderError
from damai_agent.redis_quota import RedisDailyQuota
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import JavaToolClient, ToolRegistry
from damai_agent.tracing import TraceManager


class OneResponseProvider:
    route_name = "test/model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            content="answer",
            usage=ProviderUsage(prompt_tokens=5, completion_tokens=2),
            model_route=self.route_name,
        )


class TracingAndQuotaTest(unittest.IsolatedAsyncioTestCase):
    async def test_turn_and_model_spans_share_request_trace_without_payload(self) -> None:
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracing = TraceManager(provider.get_tracer("test"), provider)
        runner = AgentRunner(
            OneResponseProvider(), ToolRegistry(), InMemorySessionStore(), tracing=tracing
        )

        result = await runner.run("secret-question", "secret-session")
        spans = exporter.get_finished_spans()

        self.assertEqual({span.name for span in spans}, {"agent.turn", "agent.model"})
        self.assertEqual({span.context.trace_id for span in spans}, {int(result.trace_id, 16)})
        self.assertNotIn("secret-question", str([span.attributes for span in spans]))
        self.assertNotIn("secret-session", str([span.attributes for span in spans]))
        tracing.shutdown()

    async def test_failed_spans_have_error_status_without_exception_payload(self) -> None:
        class FailedProvider:
            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                raise ProviderError("private-provider-token")

        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracing = TraceManager(provider.get_tracer("test"), provider)
        runner = AgentRunner(
            FailedProvider(), ToolRegistry(), InMemorySessionStore(), tracing=tracing
        )
        with self.assertRaises(ProviderError):
            await runner.run("private-question", "session")
        spans = exporter.get_finished_spans()
        self.assertEqual({span.status.status_code for span in spans}, {StatusCode.ERROR})
        self.assertNotIn("private-provider-token", str(spans))
        tracing.shutdown()

    async def test_java_traceparent_uses_active_otel_trace(self) -> None:
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracing = TraceManager(provider.get_tracer("test"), provider)
        observed: list[str] = []

        class JavaResponse:
            status = 200

            def __init__(self, traceparent: str) -> None:
                self.headers = {"traceparent": traceparent}

            def __enter__(self) -> JavaResponse:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"success": True, "code": 0, "data": {}}).encode()

        def fake_urlopen(request: urllib.request.Request, timeout: float) -> JavaResponse:
            parent = request.get_header("Traceparent")
            assert parent is not None
            observed.append(parent)
            return JavaResponse(parent)

        with patch("damai_agent.tools.urllib.request.urlopen", side_effect=fake_urlopen):
            with tracing.span("agent.turn", trace_id="1" * 32):
                result = await JavaToolClient("http://java.example", "key", 1).post(
                    "/internal/agent/test",
                    {},
                    ToolContext("session", "turn", "call", "2" * 32),
                )
        self.assertTrue(result.success)
        self.assertTrue(observed[0].startswith("00-" + "1" * 32 + "-"))
        tracing.shutdown()

    async def test_runner_fails_closed_on_shared_quota(self) -> None:
        quota = AsyncMock()
        quota.exhausted.return_value = False
        quota.charge.return_value = False
        provider = OneResponseProvider()
        runner = AgentRunner(
            provider,
            ToolRegistry(),
            InMemorySessionStore(),
            pricing_catalog={
                "test/model": ModelPrice(
                    version="v1",
                    prompt_micro_usd_per_million=1_000_000,
                    completion_micro_usd_per_million=1_000_000,
                )
            },
            tenant_quota=quota,
            tenant_daily_cost_micro_usd=5,
        )
        result = await runner.run("hello", "quota-session")
        self.assertEqual(result.error_code, AgentErrorCode.TENANT_QUOTA_EXCEEDED)
        quota.charge.assert_awaited_once()
        self.assertEqual(quota.charge.await_args.args[3], 7)
        self.assertEqual(provider.calls, 1)

    async def test_exhausted_quota_prevents_model_call(self) -> None:
        quota = AsyncMock()
        quota.exhausted.return_value = True
        provider = OneResponseProvider()
        runner = AgentRunner(
            provider,
            ToolRegistry(),
            InMemorySessionStore(),
            tenant_quota=quota,
            tenant_daily_cost_micro_usd=5,
        )
        result = await runner.run("hello", "quota-preflight")
        self.assertEqual(result.error_code, AgentErrorCode.TENANT_QUOTA_EXCEEDED)
        self.assertEqual(provider.calls, 0)

    def test_quota_keys_hide_tenant_and_turn(self) -> None:
        quota = RedisDailyQuota(AsyncMock(spec=Redis))
        keys = quota.keys_for("private-tenant", "private-turn", 1)
        self.assertNotIn("private-tenant", str(keys))
        self.assertNotIn("private-turn", str(keys))
        self.assertEqual(keys[0].split("}")[0], keys[1].split("}")[0])


@unittest.skipUnless(os.environ.get("DAMAI_TEST_REDIS_HOST"), "test Redis host not set")
class RedisQuotaIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = Redis(
            host=os.environ["DAMAI_TEST_REDIS_HOST"],
            port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
            username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
            password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
            decode_responses=True,
        )
        self.quota = RedisDailyQuota(
            self.client, key_prefix=f"damai:agent:quota-test:{uuid.uuid4().hex}:"
        )
        self.tenant = uuid.uuid4().hex

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_atomic_charge_idempotence_limit_and_isolation(self) -> None:
        self.assertFalse(await self.quota.exhausted(self.tenant, 10))
        self.assertTrue(await self.quota.charge(self.tenant, "turn-1", 1, 7, 10))
        self.assertTrue(await self.quota.charge(self.tenant, "turn-1", 1, 7, 10))
        self.assertFalse(await self.quota.charge(self.tenant, "turn-2", 1, 4, 10))
        self.assertTrue(await self.quota.exhausted(self.tenant, 10))
        self.assertFalse(await self.quota.exhausted("other-tenant", 10))
        with self.assertRaises(ResponseError):
            await self.quota.charge(self.tenant, "turn-1", 1, 8, 10)

    async def test_concurrent_charges_are_serialized_and_overage_is_accounted(self) -> None:
        outcomes = await asyncio.gather(
            *(self.quota.charge(self.tenant, f"turn-{index}", 1, 3, 10) for index in range(10))
        )
        self.assertEqual(sum(outcomes), 3)
        daily_key, _ = self.quota.keys_for(self.tenant, "turn-0", 1)
        self.assertEqual(int(await self.client.get(daily_key)), 30)
