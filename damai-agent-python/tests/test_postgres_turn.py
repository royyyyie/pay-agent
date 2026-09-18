from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
import uuid
from pathlib import Path

import psycopg

from damai_agent.checkpoint import AgentCheckpoint
from damai_agent.models import AgentRunResult, ChatMessage, ToolCall, ToolResult
from damai_agent.postgres_checkpoint import PostgresCheckpointRepository
from damai_agent.postgres_turn import (
    PostgresTurnRepository,
    TurnConflict,
    _message_payload,
    _read_message,
    _read_result,
    _result_payload,
)

_MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"
_MIGRATIONS = tuple(
    (_MIGRATION_DIR / name).read_text(encoding="utf-8")
    for name in ("001_agent_active_checkpoint.sql", "002_agent_session_turn.sql")
)


def make_messages() -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(role="user", content="查票"),
        ChatMessage(role="assistant", content="已查询"),
    )


def make_result(
    session_key: str, turn_id: str, messages: tuple[ChatMessage, ...]
) -> AgentRunResult:
    return AgentRunResult(
        session_key=session_key,
        turn_id=turn_id,
        trace_id="trace-1",
        final_content="已查询",
        tools_used=(),
        messages=messages,
    )


class TurnCodecTest(unittest.TestCase):
    def test_round_trip_preserves_turn_result_and_messages(self) -> None:
        messages = make_messages()
        result = make_result("session-1", "turn-1", messages)
        self.assertEqual(_read_result(_result_payload(result)), result)
        self.assertEqual(_read_message(_message_payload(messages[0])), messages[0])

    def test_invalid_stored_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid stored message"):
            _read_message({"role": 123})
        with self.assertRaisesRegex(ValueError, "invalid stored turn result"):
            _read_result({"turn_id": "missing-fields"})


@unittest.skipUnless(os.environ.get("DAMAI_TEST_POSTGRES_DSN"), "test PostgreSQL DSN not set")
class PostgresTurnIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dsn = os.environ["DAMAI_TEST_POSTGRES_DSN"]
        self.repository = PostgresTurnRepository(self.dsn)
        self.checkpoints = PostgresCheckpointRepository(self.dsn)
        self.session_key = f"turn-{uuid.uuid4().hex}"
        self.fingerprint = hashlib.sha256(b"same-request").hexdigest()
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            for migration in _MIGRATIONS:
                await conn.execute(migration)

    async def test_idempotent_completion_and_recent_messages(self) -> None:
        claim = await self.repository.begin_turn(
            "tenant-1", self.session_key, "turn-1", "idem-1", self.fingerprint
        )
        self.assertEqual(claim.state, "started")
        self.assertEqual(claim.fence_version, 1)
        duplicate = await self.repository.begin_turn(
            "tenant-1", self.session_key, "ignored-turn", "idem-1", self.fingerprint
        )
        self.assertEqual(duplicate.state, "in_progress")
        self.assertIsNone(duplicate.fence_version)

        messages = make_messages()
        result = make_result(self.session_key, "turn-1", messages)
        await self.repository.complete_turn(claim, result, messages)
        self.assertIsNone(await self.repository.get_active_turn("tenant-1", self.session_key))
        self.assertEqual(
            await self.repository.load_recent_messages("tenant-1", self.session_key), messages
        )
        completed = await self.repository.begin_turn(
            "tenant-1", self.session_key, "ignored-turn", "idem-1", self.fingerprint
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.result, result)
        with self.assertRaises(TurnConflict):
            await self.repository.complete_turn(claim, result, messages)

    async def test_busy_session_and_reused_idempotency_key_are_rejected(self) -> None:
        await self.repository.begin_turn(
            "tenant-1", self.session_key, "turn-1", "idem-1", self.fingerprint
        )
        with self.assertRaises(TurnConflict):
            await self.repository.begin_turn(
                "tenant-1", self.session_key, "turn-2", "idem-2", self.fingerprint
            )
        with self.assertRaises(TurnConflict):
            await self.repository.begin_turn(
                "tenant-1", self.session_key, "turn-2", "idem-1", "0" * 64
            )
        other_tenant = await self.repository.begin_turn(
            "tenant-2", self.session_key, "turn-1", "idem-1", self.fingerprint
        )
        self.assertEqual(other_tenant.state, "started")

    async def test_concurrent_duplicate_request_starts_only_one_turn(self) -> None:
        outcomes = await asyncio.gather(
            self.repository.begin_turn(
                "tenant-1", self.session_key, "turn-a", "idem-1", self.fingerprint
            ),
            self.repository.begin_turn(
                "tenant-1", self.session_key, "turn-b", "idem-1", self.fingerprint
            ),
        )
        self.assertEqual({outcome.state for outcome in outcomes}, {"started", "in_progress"})
        self.assertEqual(outcomes[0].turn_id, outcomes[1].turn_id)
        self.assertEqual(sum(outcome.fence_version is not None for outcome in outcomes), 1)

    async def test_takeover_fences_old_writer(self) -> None:
        old = await self.repository.begin_turn(
            "tenant-1", self.session_key, "turn-1", "idem-1", self.fingerprint
        )
        recovery = await self.repository.get_active_turn("tenant-1", self.session_key)
        assert recovery is not None
        self.assertEqual(
            (recovery.turn_id, recovery.state, recovery.fence_version),
            ("turn-1", "in_progress", 1),
        )
        current = await self.repository.take_over_turn(recovery)
        self.assertEqual(current.fence_version, 2)
        messages = make_messages()
        result = make_result(self.session_key, "turn-1", messages)
        with self.assertRaises(TurnConflict):
            await self.repository.complete_turn(old, result, messages)
        with self.assertRaises(TurnConflict):
            await self.repository.take_over_turn(recovery)
        await self.repository.complete_turn(current, result, messages)
        self.assertEqual(
            await self.repository.load_recent_messages("tenant-1", self.session_key), messages
        )

    async def test_checkpoint_clear_and_turn_commit_are_atomic(self) -> None:
        claim = await self.repository.begin_turn(
            "tenant-1", self.session_key, "turn-1", "idem-1", self.fingerprint
        )
        tool_call = ToolCall("call-1", "search", {"keyword": "演唱会"})
        assistant_call = ChatMessage(role="assistant", tool_calls=[tool_call])
        checkpoint = AgentCheckpoint(
            tenant_id="tenant-1",
            session_key=self.session_key,
            turn_id="turn-1",
            iteration=1,
            assistant_message=assistant_call,
            prompt_version="prompt@1",
            toolset_version="tools@1",
            policy_version="policy@1",
            model_route="demo/model",
        )
        await self.checkpoints.create(checkpoint)
        updated = checkpoint.with_result("call-1", ToolResult(True, 0, "ok"))
        await self.checkpoints.update(updated, expected_version=0)
        messages = (
            ChatMessage(role="user", content="查票"),
            assistant_call,
            ChatMessage(
                role="tool",
                content=updated.completed_results[0][1].to_model_content(),
                name="search",
                tool_call_id="call-1",
            ),
            ChatMessage(role="assistant", content="已查询"),
        )
        result = make_result(self.session_key, "turn-1", messages)
        with self.assertRaises(TurnConflict):
            await self.repository.complete_turn(
                claim, result, messages, expected_checkpoint_version=0
            )
        self.assertEqual(
            await self.repository.load_recent_messages("tenant-1", self.session_key), ()
        )
        self.assertEqual(await self.checkpoints.get_active("tenant-1", self.session_key), updated)
        await self.repository.complete_turn(claim, result, messages, expected_checkpoint_version=1)
        self.assertIsNone(await self.checkpoints.get_active("tenant-1", self.session_key))
        self.assertEqual(
            await self.repository.load_recent_messages("tenant-1", self.session_key), messages
        )
