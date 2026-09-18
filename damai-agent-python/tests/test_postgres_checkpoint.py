from __future__ import annotations

import asyncio
import copy
import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg

from damai_agent.checkpoint import AgentCheckpoint, CheckpointConflict
from damai_agent.models import AgentErrorCode, ChatMessage, ToolCall, ToolResult
from damai_agent.postgres_checkpoint import (
    PostgresCheckpointRepository,
    _decode,
    _encode,
)

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[1] / "migrations" / "001_agent_active_checkpoint.sql"
).read_text(encoding="utf-8")


def make_checkpoint(session_key: str = "session-1", tenant_id: str = "tenant-1") -> AgentCheckpoint:
    return AgentCheckpoint(
        tenant_id=tenant_id,
        session_key=session_key,
        turn_id="turn-1",
        iteration=1,
        assistant_message=ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall("call-1", "search", {"keyword": "演唱会"}),
                ToolCall("call-2", "detail", {"programId": 123}),
            ],
        ),
        prompt_version="prompt@1",
        toolset_version="tools@1",
        policy_version="policy@1",
        model_route="demo/model",
    )


class CheckpointCodecTest(unittest.TestCase):
    def test_round_trip_preserves_result_and_error_enum(self) -> None:
        original = make_checkpoint().with_result(
            "call-1",
            ToolResult(
                success=False,
                code=503,
                message="timeout",
                data={"retry": False},
                error_code=AgentErrorCode.TOOL_TIMEOUT,
            ),
        )

        loaded = _decode(_encode(original))

        self.assertEqual(loaded, original)
        self.assertIs(loaded.completed_results[0][1].error_code, AgentErrorCode.TOOL_TIMEOUT)

    def test_invalid_payload_is_rejected(self) -> None:
        payload = _encode(make_checkpoint())
        payload["version"] = "not-an-integer"
        with self.assertRaisesRegex(ValueError, "invalid checkpoint payload"):
            _decode(payload)


@unittest.skipUnless(os.environ.get("DAMAI_TEST_POSTGRES_DSN"), "test PostgreSQL DSN not set")
class PostgresCheckpointIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dsn = os.environ["DAMAI_TEST_POSTGRES_DSN"]
        self.repository = PostgresCheckpointRepository(self.dsn)
        self.session_key = f"checkpoint-{uuid.uuid4().hex}"
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            await conn.execute(_MIGRATION_SQL)

    async def test_create_reload_update_and_clear(self) -> None:
        original = make_checkpoint(self.session_key)
        await self.repository.create(original)
        with self.assertRaises(CheckpointConflict):
            await self.repository.create(original)

        original.assistant_message.tool_calls[0].arguments["keyword"] = "mutated"
        loaded = await self.repository.get_active("tenant-1", self.session_key)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.assistant_message.tool_calls[0].arguments, {"keyword": "演唱会"})

        updated = loaded.with_result("call-2", ToolResult(True, 0, "ok", {"id": 123}))
        await self.repository.update(updated, expected_version=0)
        self.assertEqual(await self.repository.get_active("tenant-1", self.session_key), updated)
        with self.assertRaises(CheckpointConflict):
            await self.repository.clear("tenant-1", self.session_key, "turn-1", 0)
        await self.repository.clear("tenant-1", self.session_key, "turn-1", 1)
        self.assertIsNone(await self.repository.get_active("tenant-1", self.session_key))

    async def test_concurrent_writers_only_one_commits(self) -> None:
        original = make_checkpoint(self.session_key)
        await self.repository.create(original)
        first = original.with_result("call-1", ToolResult(True, 0, "first"))
        second = original.with_result("call-2", ToolResult(True, 0, "second"))

        outcomes = await asyncio.gather(
            self.repository.update(first, expected_version=0),
            self.repository.update(second, expected_version=0),
            return_exceptions=True,
        )

        self.assertEqual(sum(outcome is None for outcome in outcomes), 1)
        self.assertEqual(sum(isinstance(outcome, CheckpointConflict) for outcome in outcomes), 1)
        loaded = await self.repository.get_active("tenant-1", self.session_key)
        self.assertIn(loaded, (first, second))

    async def test_tenant_isolation_and_invalid_transition(self) -> None:
        first = make_checkpoint(self.session_key)
        second = make_checkpoint(self.session_key, tenant_id="tenant-2")
        await self.repository.create(first)
        await self.repository.create(second)
        self.assertEqual(await self.repository.get_active("tenant-2", self.session_key), second)

        forged = copy.deepcopy(first.with_result("call-1", ToolResult(True, 0, "ok")))
        forged.assistant_message.tool_calls[0].arguments["keyword"] = "changed"
        with self.assertRaises(CheckpointConflict):
            await self.repository.update(forged, expected_version=0)
        self.assertEqual(await self.repository.get_active("tenant-1", self.session_key), first)

    async def test_stale_clear_cannot_remove_new_turn(self) -> None:
        original = make_checkpoint(self.session_key)
        await self.repository.create(original)
        await self.repository.clear("tenant-1", self.session_key, "turn-1", 0)
        replacement = replace(make_checkpoint(self.session_key), turn_id="turn-2")
        await self.repository.create(replacement)
        with self.assertRaises(CheckpointConflict):
            await self.repository.clear("tenant-1", self.session_key, "turn-1", 0)
        self.assertEqual(
            await self.repository.get_active("tenant-1", self.session_key), replacement
        )
