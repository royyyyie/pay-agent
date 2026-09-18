from __future__ import annotations

import asyncio
import json
import unittest

from damai_agent.checkpoint import (
    AgentCheckpoint,
    CheckpointConflict,
    InMemoryCheckpointRepository,
)
from damai_agent.models import ChatMessage, ToolCall, ToolResult
from damai_agent.runtime.context import valid_tool_protocol


def make_checkpoint(turn_id: str = "turn-1") -> AgentCheckpoint:
    return AgentCheckpoint(
        tenant_id="tenant-1",
        session_key="session-1",
        turn_id=turn_id,
        iteration=1,
        assistant_message=ChatMessage(
            role="assistant",
            tool_calls=[ToolCall("call-1", "search", {}), ToolCall("call-2", "detail", {})],
        ),
        prompt_version="prompt@1",
        toolset_version="tools@1",
        policy_version="policy@1",
        model_route="test/model",
    )


class CheckpointTest(unittest.IsolatedAsyncioTestCase):
    def test_missing_result_is_repaired_as_unknown_without_replay(self) -> None:
        checkpoint = make_checkpoint().with_result(
            "call-2", ToolResult(success=True, code=0, message="ok", data={"id": 2})
        )

        repaired = checkpoint.repaired_messages()

        self.assertEqual(checkpoint.phase, "tool_progress")
        self.assertEqual(checkpoint.pending_tool_call_ids, ("call-1",))
        self.assertEqual([message.tool_call_id for message in repaired[1:]], ["call-1", "call-2"])
        self.assertEqual(
            json.loads(repaired[1].content or "{}")["errorCode"], "TOOL_EXECUTION_UNKNOWN"
        )
        self.assertFalse(json.loads(repaired[1].content or "{}")["retryable"])
        self.assertTrue(json.loads(repaired[2].content or "{}")["success"])
        self.assertTrue(
            valid_tool_protocol([ChatMessage(role="system"), ChatMessage(role="user"), *repaired])
        )

    def test_complete_batch_keeps_results_in_original_call_order(self) -> None:
        checkpoint = make_checkpoint()
        checkpoint = checkpoint.with_result("call-2", ToolResult(True, 0, "second"))
        checkpoint = checkpoint.with_result("call-1", ToolResult(True, 0, "first"))

        repaired = checkpoint.repaired_messages()

        self.assertEqual(checkpoint.phase, "ready_to_resume")
        self.assertEqual(checkpoint.pending_tool_call_ids, ())
        self.assertEqual([message.name for message in repaired[1:]], ["search", "detail"])
        self.assertEqual(json.loads(repaired[1].content or "{}")["message"], "first")
        self.assertEqual(json.loads(repaired[2].content or "{}")["message"], "second")

    def test_checkpoint_rejects_invalid_batches_and_duplicate_results(self) -> None:
        with self.assertRaises(ValueError):
            AgentCheckpoint(
                tenant_id="tenant-1",
                session_key="session-1",
                turn_id="turn-1",
                iteration=1,
                assistant_message=ChatMessage(
                    role="assistant",
                    tool_calls=[ToolCall("same", "search", {}), ToolCall("same", "detail", {})],
                ),
                prompt_version="p",
                toolset_version="t",
                policy_version="r",
                model_route="m",
            )

        checkpoint = make_checkpoint().with_result("call-1", ToolResult(True, 0, "ok"))
        with self.assertRaises(ValueError):
            checkpoint.with_result("call-1", ToolResult(True, 0, "duplicate"))
        with self.assertRaises(ValueError):
            checkpoint.with_result("not-in-batch", ToolResult(True, 0, "unknown"))

    async def test_create_update_clear_use_version_and_one_active_turn_per_session(self) -> None:
        repository = InMemoryCheckpointRepository()
        first = make_checkpoint()
        await repository.create(first)

        with self.assertRaises(CheckpointConflict):
            await repository.create(make_checkpoint("turn-2"))

        updated = first.with_result("call-1", ToolResult(True, 0, "ok"))
        await repository.update(updated, expected_version=0)
        with self.assertRaises(CheckpointConflict):
            await repository.update(updated, expected_version=0)
        with self.assertRaises(CheckpointConflict):
            await repository.clear("tenant-1", "session-1", "turn-1", expected_version=0)

        loaded = await repository.get_active("tenant-1", "session-1")
        self.assertEqual(loaded, updated)
        await repository.clear("tenant-1", "session-1", "turn-1", expected_version=1)
        self.assertIsNone(await repository.get_active("tenant-1", "session-1"))

    async def test_concurrent_writers_cannot_overwrite_each_other(self) -> None:
        repository = InMemoryCheckpointRepository()
        checkpoint = make_checkpoint()
        await repository.create(checkpoint)
        first = checkpoint.with_result("call-1", ToolResult(True, 0, "first"))
        second = checkpoint.with_result("call-2", ToolResult(True, 0, "second"))

        outcomes = await asyncio.gather(
            repository.update(first, expected_version=0),
            repository.update(second, expected_version=0),
            return_exceptions=True,
        )

        self.assertEqual(sum(outcome is None for outcome in outcomes), 1)
        self.assertEqual(sum(isinstance(outcome, CheckpointConflict) for outcome in outcomes), 1)
        loaded = await repository.get_active("tenant-1", "session-1")
        self.assertEqual(loaded.version if loaded else None, 1)

    async def test_repository_defensively_copies_mutable_tool_arguments(self) -> None:
        repository = InMemoryCheckpointRepository()
        checkpoint = make_checkpoint()
        await repository.create(checkpoint)
        checkpoint.assistant_message.tool_calls[0].arguments["keyword"] = "mutated"

        loaded = await repository.get_active("tenant-1", "session-1")

        self.assertEqual(loaded.assistant_message.tool_calls[0].arguments if loaded else None, {})


if __name__ == "__main__":
    unittest.main()
