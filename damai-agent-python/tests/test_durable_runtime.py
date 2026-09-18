from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import AsyncMock, patch

import psycopg
from redis.asyncio import Redis

from damai_agent.models import (
    AgentRunSpec,
    ChatMessage,
    ProviderResponse,
    TicketTurnContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from damai_agent.postgres_checkpoint import PostgresCheckpointRepository
from damai_agent.postgres_turn import PostgresTurnRepository, TurnConflict
from damai_agent.redis_lease import RedisSessionLeaseStore
from damai_agent.runtime.durable import DurableTurnService, LeaseLost
from damai_agent.runtime.events import TurnEventEmitter
from damai_agent.runtime.runner import ToolCallingRunner
from damai_agent.tools import AgentTool, ToolRegistry

_MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"
_MIGRATIONS = tuple(
    (_MIGRATION_DIR / name).read_text(encoding="utf-8")
    for name in ("001_agent_active_checkpoint.sql", "002_agent_session_turn.sql")
)


class CountingTool(AgentTool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="read-only test tool",
            parameters={"type": "object", "properties": {}},
            required_scope="programs:read",
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(True, 0, "ok", {"programId": 1})


class ScriptedProvider:
    route_name = "test/model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[ToolCall("call-1", "search_programs", {})],
                finish_reason="tool_calls",
                model_route=self.route_name,
            )
        if messages[-1].role != "tool":
            raise AssertionError("missing persisted tool observation")
        return ProviderResponse(content="找到节目", model_route=self.route_name)


def make_context(session_key: str, turn_id: str = "turn-1") -> TicketTurnContext:
    return TicketTurnContext(
        tenant_id="tenant-durable-test",
        user_id="user-1",
        session_key=session_key,
        turn_id=turn_id,
        request_id=f"request-{uuid.uuid4().hex}",
        trace_id=uuid.uuid4().hex,
        locale="zh-CN",
        channel="test",
        tool_scopes=frozenset({"programs:read"}),
        risk_ceiling=ToolRisk.READ_ONLY,
        delegation_token_id="test-delegation",
    )


class RunnerCheckpointBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_failure_prevents_tool_execution(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        context = make_context("session-unit")
        spec = AgentRunSpec(
            context=context,
            messages=(ChatMessage(role="user", content="查票"),),
            tool_specs=(tool.spec,),
            system_prompt="test",
            prompt_version="p1",
            toolset_version="t1",
            policy_version="r1",
            model_route="test/model",
        )
        recorder = AsyncMock()
        recorder.before_tool_round.side_effect = RuntimeError("checkpoint unavailable")

        with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
            await runner.run(spec, TurnEventEmitter(context, None), recorder=recorder)

        self.assertEqual(tool.calls, 0)
        self.assertEqual(provider.calls, 1)
        recorder.record_tool_result.assert_not_awaited()

    async def test_result_write_failure_stops_before_next_model_call(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        context = make_context("session-result-failure")
        spec = AgentRunSpec(
            context=context,
            messages=(ChatMessage(role="user", content="查票"),),
            tool_specs=(tool.spec,),
            system_prompt="test",
            prompt_version="p1",
            toolset_version="t1",
            policy_version="r1",
            model_route="test/model",
        )
        recorder = AsyncMock()
        recorder.record_tool_result.side_effect = RuntimeError("result write unavailable")

        with self.assertRaisesRegex(RuntimeError, "result write unavailable"):
            await runner.run(spec, TurnEventEmitter(context, None), recorder=recorder)

        self.assertEqual(tool.calls, 1)
        self.assertEqual(provider.calls, 1)
        recorder.commit_tool_round.assert_not_awaited()

    async def test_durable_entry_rejects_write_ceiling(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        service = DurableTurnService(runner, AsyncMock(), AsyncMock())
        context = make_context("session-write-ceiling")
        context = TicketTurnContext(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_key=context.session_key,
            turn_id=context.turn_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            locale=context.locale,
            channel=context.channel,
            tool_scopes=context.tool_scopes,
            risk_ceiling=ToolRisk.ORDER_WRITE,
            delegation_token_id=context.delegation_token_id,
        )
        with self.assertRaisesRegex(ValueError, "read-only"):
            await service.run("查票", context, "idem-1")


@unittest.skipUnless(
    os.environ.get("DAMAI_TEST_POSTGRES_DSN") and os.environ.get("DAMAI_TEST_REDIS_HOST"),
    "test PostgreSQL and Redis connections not set",
)
class DurableRuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dsn = os.environ["DAMAI_TEST_POSTGRES_DSN"]
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            for migration in _MIGRATIONS:
                await conn.execute(migration)
        self.client = Redis(
            host=os.environ["DAMAI_TEST_REDIS_HOST"],
            port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
            username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
            password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
        self.turns = PostgresTurnRepository(self.dsn)
        self.checkpoints = PostgresCheckpointRepository(self.dsn)
        self.leases = RedisSessionLeaseStore(
            self.client, key_prefix=f"damai:agent:acceptance:{uuid.uuid4().hex}:"
        )
        self.session_key = f"durable-{uuid.uuid4().hex}"
        self.tool = CountingTool()
        self.provider = ScriptedProvider()
        self.runner = ToolCallingRunner(self.provider, ToolRegistry([self.tool]))
        self.service = DurableTurnService(self.runner, self.turns, self.leases)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_completed_duplicate_returns_same_result_without_reexecuting_tool(self) -> None:
        context = make_context(self.session_key)
        events: list[dict[str, Any]] = []
        result = await self.service.run("查票", context, "idem-1", events.append)
        self.assertEqual(result.final_content, "找到节目")
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))
        self.assertEqual(
            await self.turns.load_recent_messages(context.tenant_id, self.session_key),
            result.messages,
        )

        duplicate_context = make_context(self.session_key, "turn-2")
        duplicate = await self.service.run("查票", duplicate_context, "idem-1")
        self.assertEqual(duplicate, result)
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.provider.calls, 2)

    async def test_progress_failure_leaves_checkpoint_and_does_not_replay(self) -> None:
        context = make_context(self.session_key)
        with patch.object(
            self.turns,
            "append_tool_round_progress",
            new=AsyncMock(side_effect=RuntimeError("progress unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "progress unavailable"):
                await self.service.run("查票", context, "idem-1")

        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.provider.calls, 1)
        checkpoint = await self.checkpoints.get_active(context.tenant_id, self.session_key)
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.phase, "ready_to_resume")
        with self.assertRaises(TurnConflict):
            await self.service.run("查票", make_context(self.session_key, "turn-2"), "idem-1")
        self.assertEqual(self.tool.calls, 1)

    async def test_lost_lease_stops_before_tool_dispatch(self) -> None:
        context = make_context(self.session_key)
        with patch.object(
            self.leases,
            "renew",
            new=AsyncMock(side_effect=[True, True, False]),
        ):
            with self.assertRaises(LeaseLost):
                await self.service.run("查票", context, "idem-1")
        self.assertEqual(self.tool.calls, 0)
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))
