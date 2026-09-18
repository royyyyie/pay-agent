from __future__ import annotations

import asyncio
import json
import os
import sys
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
from damai_agent.postgres_turn import PostgresTurnRepository, TurnClaim, TurnConflict
from damai_agent.redis_control import RedisTurnCancellationStore
from damai_agent.redis_lease import RedisSessionLeaseStore, SessionLease
from damai_agent.redis_queue import RedisPendingTurnQueue
from damai_agent.runtime.durable import DurableTurnService, LeaseLost, SessionBusy, TurnCancelled
from damai_agent.runtime.events import TurnEventEmitter
from damai_agent.runtime.runner import ToolCallingRunner
from damai_agent.tools import AgentTool, ToolRegistry

_MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"
_KILLABLE_CHILD = Path(__file__).resolve().parent / "helpers" / "killable_turn.py"
_MIGRATIONS = tuple(
    (_MIGRATION_DIR / name).read_text(encoding="utf-8")
    for name in (
        "001_agent_active_checkpoint.sql",
        "002_agent_session_turn.sql",
        "003_agent_turn_event.sql",
    )
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

    async def test_recovery_without_checkpoint_never_calls_provider_or_tool(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        turns = AsyncMock(spec=PostgresTurnRepository)
        leases = AsyncMock(spec=RedisSessionLeaseStore)
        context = make_context("session-recovery-unit", "new-request-turn")
        leases.acquire.return_value = SessionLease("test-key", "test-owner", 30_000)
        leases.renew.return_value = True
        turns.get_recoverable_turn.return_value = TurnClaim(
            context.tenant_id, context.session_key, "original-turn", "in_progress", 1
        )
        turns.take_over_turn.return_value = TurnClaim(
            context.tenant_id, context.session_key, "original-turn", "started", 2
        )
        turns.load_turn_messages.return_value = ()
        turns.get_checkpoint_for_turn.return_value = None
        service = DurableTurnService(runner, turns, leases)

        result = await service.recover("查票", context, "idem-1")

        self.assertEqual(result.turn_id, "original-turn")
        self.assertEqual([message.role for message in result.messages], ["user", "assistant"])
        self.assertEqual(provider.calls, 0)
        self.assertEqual(tool.calls, 0)
        turns.get_recoverable_turn.assert_awaited_once()
        turns.begin_turn.assert_not_awaited()
        turns.complete_turn.assert_awaited_once()
        leases.release.assert_awaited_once()

    async def test_durable_run_persists_events_before_forwarding(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        turns = AsyncMock(spec=PostgresTurnRepository)
        leases = AsyncMock(spec=RedisSessionLeaseStore)
        context = make_context("session-event-unit")
        leases.acquire.return_value = SessionLease("test-key", "test-owner", 30_000)
        leases.renew.return_value = True
        turns.begin_turn.return_value = TurnClaim(
            context.tenant_id, context.session_key, context.turn_id, "started", 1
        )
        turns.load_recent_messages.return_value = ()
        sequence = 0
        stored_events: list[dict[str, Any]] = []

        async def record_event(_: TurnClaim, event: dict[str, Any]) -> dict[str, Any]:
            nonlocal sequence
            sequence += 1
            recorded = {**event, "eventSeq": sequence, "eventId": str(sequence)}
            stored_events.append(recorded)
            return recorded

        async def finish_turn(
            _: TurnClaim,
            __: Any,
            ___: Any,
            *,
            final_event: dict[str, Any],
        ) -> dict[str, Any]:
            return await record_event(turns.begin_turn.return_value, final_event)

        turns.append_event.side_effect = record_event
        turns.complete_turn.side_effect = finish_turn
        delivered: list[dict[str, Any]] = []
        service = DurableTurnService(runner, turns, leases)

        result = await service.run("查票", context, "idem-1", delivered.append)

        self.assertEqual(result.final_content, "找到节目")
        self.assertEqual(tool.calls, 1)
        self.assertEqual(delivered, stored_events)
        self.assertEqual(delivered[-1]["type"], "turn.completed")
        self.assertEqual(delivered[-1]["eventId"], str(len(delivered)))
        turns.create_checkpoint.assert_awaited_once()
        turns.update_checkpoint.assert_awaited_once()
        turns.append_tool_round_progress.assert_awaited_once()

    async def test_pending_head_blocks_later_request_before_lease(self) -> None:
        provider = ScriptedProvider()
        tool = CountingTool()
        runner = ToolCallingRunner(provider, ToolRegistry([tool]))
        turns = AsyncMock(spec=PostgresTurnRepository)
        leases = AsyncMock(spec=RedisSessionLeaseStore)
        pending = AsyncMock(spec=RedisPendingTurnQueue)
        pending.head.return_value = "a" * 64
        pending.enqueue.return_value = 2
        service = DurableTurnService(runner, turns, leases, pending_queue=pending)
        context = make_context("session-pending-unit")

        self.assertEqual(await service.enqueue("查票", context, "idem-2"), 2)
        with self.assertRaises(TurnConflict):
            await service.run("查票", context, "idem-2")
        leases.acquire.assert_not_awaited()
        turns.begin_turn.assert_not_awaited()

    async def test_background_lease_loss_cancels_slow_model(self) -> None:
        class SlowProvider:
            route_name = "test/slow"

            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                await asyncio.sleep(1)
                return ProviderResponse(content="late")

        tool = CountingTool()
        runner = ToolCallingRunner(SlowProvider(), ToolRegistry([tool]))
        turns = AsyncMock(spec=PostgresTurnRepository)
        leases = AsyncMock(spec=RedisSessionLeaseStore)
        context = make_context("session-heartbeat-unit")
        leases.acquire.return_value = SessionLease("test-key", "test-owner", 100)
        leases.renew.side_effect = [True, False]
        turns.begin_turn.return_value = TurnClaim(
            context.tenant_id, context.session_key, context.turn_id, "started", 1
        )
        turns.load_recent_messages.return_value = ()
        service = DurableTurnService(runner, turns, leases, lease_ttl_ms=100)

        with self.assertRaises(LeaseLost):
            await service.run("查票", context, "idem-1")

        turns.complete_turn.assert_not_awaited()
        leases.release.assert_awaited_once()


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
        saved_events = await self.turns.load_events_after(
            context.tenant_id, self.session_key, context.turn_id, 0
        )
        self.assertEqual(saved_events, tuple(events))
        self.assertEqual(
            [event["eventSeq"] for event in saved_events],
            list(range(1, len(saved_events) + 1)),
        )
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

        recovered = await self.service.recover(
            "查票", make_context(self.session_key, "recovery-1"), "idem-1"
        )
        self.assertEqual(recovered.stop_reason, "interrupted_recovered")
        self.assertEqual(self.tool.calls, 1)
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))
        self.assertEqual(
            await self.turns.load_recent_messages(context.tenant_id, self.session_key),
            recovered.messages,
        )
        duplicate = await self.service.run("查票", make_context(self.session_key), "idem-1")
        self.assertEqual(duplicate, recovered)

    async def test_unknown_tool_result_is_recorded_without_reexecution(self) -> None:
        context = make_context(self.session_key)
        with patch.object(
            self.turns,
            "update_checkpoint",
            new=AsyncMock(side_effect=RuntimeError("result unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "result unavailable"):
                await self.service.run("查票", context, "idem-1")
        self.assertEqual(self.tool.calls, 1)
        checkpoint = await self.checkpoints.get_active(context.tenant_id, self.session_key)
        assert checkpoint is not None
        self.assertEqual(checkpoint.phase, "awaiting_tools")

        recovered = await self.service.recover(
            "查票", make_context(self.session_key, "recovery-1"), "idem-1"
        )
        self.assertEqual(recovered.error_code.value, "TOOL_EXECUTION_UNKNOWN")
        observations = [message for message in recovered.messages if message.role == "tool"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(
            json.loads(observations[0].content or "{}")["errorCode"],
            "TOOL_EXECUTION_UNKNOWN",
        )
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.provider.calls, 1)

    async def test_recovery_retry_keeps_unknown_status_after_checkpoint_clear(self) -> None:
        context = make_context(self.session_key)
        with patch.object(
            self.turns,
            "update_checkpoint",
            new=AsyncMock(side_effect=RuntimeError("result unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "result unavailable"):
                await self.service.run("查票", context, "idem-1")
        with patch.object(
            self.turns,
            "complete_turn",
            new=AsyncMock(side_effect=RuntimeError("completion unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "completion unavailable"):
                await self.service.recover("查票", context, "idem-1")
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))

        recovered = await self.service.recover("查票", context, "idem-1")
        self.assertIsNotNone(recovered.error_code)
        assert recovered.error_code is not None
        self.assertEqual(recovered.error_code.value, "TOOL_EXECUTION_UNKNOWN")
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(self.provider.calls, 1)

    async def test_recovery_rejects_wrong_request_without_taking_over(self) -> None:
        context = make_context(self.session_key)
        with patch.object(
            self.provider,
            "complete",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                await self.service.run("查票", context, "idem-1")
        before = await self.turns.get_active_turn(context.tenant_id, self.session_key)
        with self.assertRaises(TurnConflict):
            await self.service.recover("查别的", context, "idem-1")
        with self.assertRaises(TurnConflict):
            await self.service.recover("查票", context, "wrong-key")
        self.assertEqual(
            await self.turns.get_active_turn(context.tenant_id, self.session_key), before
        )
        recovered = await self.service.recover("查票", context, "idem-1")
        self.assertEqual(len(recovered.messages), 2)
        self.assertEqual(recovered.messages[0].content, "查票")
        self.assertEqual(self.tool.calls, 0)

    async def test_cancel_stops_before_tool_dispatch_and_recovery_finishes(self) -> None:
        context = make_context(self.session_key)
        cancellations = RedisTurnCancellationStore(
            self.client, key_prefix=f"damai:agent:cancel-test:{uuid.uuid4().hex}:"
        )
        service = DurableTurnService(
            self.runner, self.turns, self.leases, cancellations=cancellations
        )

        async def cancel_after_model(event: dict[str, Any]) -> None:
            if event["type"] == "model.completed":
                self.assertTrue(await service.cancel("查票", context, "idem-1"))

        with self.assertRaises(TurnCancelled):
            await service.run("查票", context, "idem-1", cancel_after_model)
        self.assertEqual(self.tool.calls, 0)
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))
        self.assertFalse(
            await service.cancel("查票", make_context(self.session_key, "wrong-turn"), "idem-1")
        )
        recovered = await service.recover(None, context, "idem-1")
        self.assertEqual(recovered.stop_reason, "interrupted_recovered")
        self.assertEqual(self.tool.calls, 0)

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

    async def test_two_service_instances_never_start_same_session_together(self) -> None:
        started = asyncio.Event()
        finish_first = asyncio.Event()

        class PausingProvider:
            route_name = "test/pausing"

            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                started.set()
                await finish_first.wait()
                return ProviderResponse(content="first complete")

        first_runner = ToolCallingRunner(PausingProvider(), ToolRegistry([CountingTool()]))
        first_service = DurableTurnService(first_runner, self.turns, self.leases)
        first_context = make_context(self.session_key, "turn-first")
        second_context = make_context(self.session_key, "turn-second")
        first_task = asyncio.create_task(first_service.run("第一问", first_context, "idem-first"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            with self.assertRaises(SessionBusy):
                await self.service.run("第二问", second_context, "idem-second")
            self.assertEqual(self.provider.calls, 0)
        finally:
            finish_first.set()
        first_result = await asyncio.wait_for(first_task, timeout=5)
        self.assertEqual(first_result.final_content, "first complete")
        second_result = await self.service.run("第二问", second_context, "idem-second")
        self.assertEqual(second_result.final_content, "找到节目")
        self.assertEqual(self.tool.calls, 1)
        self.assertEqual(
            await self.turns.get_turn_status(
                second_context.tenant_id, self.session_key, second_context.turn_id
            ),
            "completed",
        )

    async def test_cancelled_worker_requires_safe_recovery(self) -> None:
        started = asyncio.Event()

        class PausingProvider:
            route_name = "test/pausing"

            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                started.set()
                await asyncio.sleep(30)
                return ProviderResponse(content="never reached")

        provider = PausingProvider()
        service = DurableTurnService(
            ToolCallingRunner(provider, ToolRegistry([self.tool])), self.turns, self.leases
        )
        context = make_context(self.session_key)
        task = asyncio.create_task(service.run("查票", context, "idem-1"))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            await self.turns.get_turn_status(context.tenant_id, self.session_key, context.turn_id),
            "running",
        )
        recovered = await service.recover(None, context, "idem-1")
        self.assertEqual(recovered.stop_reason, "interrupted_recovered")
        self.assertEqual(self.tool.calls, 0)

    async def test_killed_process_recovers_unknown_tool_without_reexecution(self) -> None:
        key_prefix = f"damai:agent:kill-test:{uuid.uuid4().hex}:"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_KILLABLE_CHILD),
            self.session_key,
            key_prefix,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        context = make_context(self.session_key, "turn-killed")
        try:
            for _ in range(200):
                checkpoint = await self.checkpoints.get_active(context.tenant_id, self.session_key)
                if checkpoint is not None:
                    break
                if process.returncode is not None:
                    self.fail("child exited before creating a checkpoint")
                await asyncio.sleep(0.05)
            else:
                self.fail("child did not create a checkpoint in time")
            assert checkpoint is not None
            self.assertEqual(checkpoint.phase, "awaiting_tools")
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await asyncio.sleep(0.8)
        self.assertEqual(
            await self.turns.get_turn_status(context.tenant_id, self.session_key, context.turn_id),
            "running",
        )
        recovery = DurableTurnService(
            self.runner,
            self.turns,
            RedisSessionLeaseStore(self.client, key_prefix=key_prefix),
            lease_ttl_ms=300,
        )
        result = await recovery.recover(None, context, "idem-killed")
        self.assertEqual(result.error_code.value, "TOOL_EXECUTION_UNKNOWN")
        self.assertEqual(self.tool.calls, 0)
        self.assertEqual(self.provider.calls, 0)
        self.assertIsNone(await self.checkpoints.get_active(context.tenant_id, self.session_key))
