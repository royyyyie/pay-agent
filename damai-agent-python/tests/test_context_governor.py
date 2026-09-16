from __future__ import annotations

import json
import unittest
from typing import Any, Dict, Sequence

from damai_agent.models import (
    AgentErrorCode,
    ChatMessage,
    ProviderResponse,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.runner import AgentRunner
from damai_agent.runtime.context import ContextGovernor, valid_tool_protocol
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class LargeTool(AgentTool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="large_lookup", description="test", parameters={})

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, code=0, message="ok", data="secret" * 1000)


class CapturingProvider:
    def __init__(self, tool_calls: list[ToolCall] | None = None) -> None:
        self.calls = 0
        self.tool_calls = tool_calls or []
        self.observations: list[ChatMessage] = []
        self.requests: list[list[ChatMessage]] = []

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        self.requests.append(list(messages))
        if self.calls == 1 and self.tool_calls:
            return ProviderResponse(tool_calls=self.tool_calls, finish_reason="tool_calls")
        self.observations = [message for message in messages if message.role == "tool"]
        return ProviderResponse(content="done")


class ContextGovernorTest(unittest.IsolatedAsyncioTestCase):
    def test_old_turn_is_dropped_as_one_complete_unit(self) -> None:
        call = ToolCall("call-old", "lookup", {})
        messages = [
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="old" * 400),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", name="lookup", tool_call_id="call-old", content="{}"),
            ChatMessage(role="assistant", content="old answer"),
            ChatMessage(role="user", content="new question"),
        ]

        selected = ContextGovernor(512, 256).prepare(messages, [])

        self.assertIsNotNone(selected)
        self.assertEqual([message.role for message in selected or []], ["system", "user"])
        self.assertEqual(selected[-1].content if selected else None, "new question")
        self.assertTrue(valid_tool_protocol(selected or []))

    async def test_current_message_over_budget_returns_stable_error_without_model_call(
        self,
    ) -> None:
        provider = CapturingProvider()
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(),
            sessions=InMemorySessionStore(),
            max_context_chars=512,
        )

        result = await runner.run("x" * 2000, "oversized-user")

        self.assertEqual(result.error_code, AgentErrorCode.CONTEXT_BUDGET_EXCEEDED)
        self.assertEqual(provider.calls, 0)
        self.assertNotIn("x" * 100, result.answer)

    async def test_oversized_tool_result_is_bounded_and_keeps_call_pair(self) -> None:
        tool = LargeTool()
        provider = CapturingProvider([ToolCall("call-1", "large_lookup", {})])
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
            max_tool_result_chars=256,
        )

        result = await runner.run("lookup", "large-result")

        self.assertEqual(result.answer, "done")
        self.assertEqual(tool.calls, 1)
        self.assertEqual(provider.calls, 2)
        self.assertTrue(valid_tool_protocol([ChatMessage(role="system"), *result.messages]))
        self.assertEqual(len(provider.observations), 1)
        observation = provider.observations[0]
        self.assertEqual(observation.tool_call_id, "call-1")
        self.assertLessEqual(len(observation.content or ""), 256)
        self.assertEqual(
            json.loads(observation.content or "{}")["errorCode"], "TOOL_RESULT_TOO_LARGE"
        )
        self.assertNotIn("secret", observation.content or "")

    async def test_current_tool_batch_over_budget_stops_with_complete_pair(self) -> None:
        tool = LargeTool()
        provider = CapturingProvider([ToolCall("call-1", "large_lookup", {})])
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
            max_context_chars=1000,
        )

        result = await runner.run("lookup", "batch-over-budget")

        self.assertEqual(result.error_code, AgentErrorCode.CONTEXT_BUDGET_EXCEEDED)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(tool.calls, 1)
        self.assertTrue(valid_tool_protocol([ChatMessage(role="system"), *result.messages]))
        self.assertEqual(result.messages[-2].tool_call_id, "call-1")

    async def test_duplicate_tool_call_ids_stop_before_execution(self) -> None:
        tool = LargeTool()
        provider = CapturingProvider(
            [ToolCall("same-id", "large_lookup", {}), ToolCall("same-id", "large_lookup", {})]
        )
        runner = AgentRunner(
            provider=provider, registry=ToolRegistry([tool]), sessions=InMemorySessionStore()
        )

        result = await runner.run("lookup", "duplicate-calls")

        self.assertEqual(result.error_code, AgentErrorCode.CONTEXT_PROTOCOL_INVALID)
        self.assertEqual(tool.calls, 0)
        self.assertEqual(provider.calls, 1)

    async def test_malformed_tool_call_id_stops_without_crashing(self) -> None:
        tool = LargeTool()
        provider = CapturingProvider([ToolCall(123, "large_lookup", {})])  # type: ignore[arg-type]
        runner = AgentRunner(
            provider=provider, registry=ToolRegistry([tool]), sessions=InMemorySessionStore()
        )

        result = await runner.run("lookup", "malformed-call")

        self.assertEqual(result.error_code, AgentErrorCode.CONTEXT_PROTOCOL_INVALID)
        self.assertEqual(tool.calls, 0)

    async def test_orphaned_historical_tool_result_stops_before_model_call(self) -> None:
        sessions = InMemorySessionStore()
        await sessions.append(
            "orphaned", [ChatMessage(role="tool", tool_call_id="orphan", content="{}")]
        )
        provider = CapturingProvider()
        runner = AgentRunner(provider=provider, registry=ToolRegistry(), sessions=sessions)

        result = await runner.run("new question", "orphaned")

        self.assertEqual(result.error_code, AgentErrorCode.CONTEXT_PROTOCOL_INVALID)
        self.assertEqual(provider.calls, 0)

    async def test_session_retention_preserves_whole_turns(self) -> None:
        sessions = InMemorySessionStore(max_messages=5)
        call = ToolCall("call-1", "lookup", {})
        first = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", name="lookup", tool_call_id="call-1", content="{}"),
            ChatMessage(role="assistant", content="answer"),
        ]
        second = [ChatMessage(role="user", content="second"), ChatMessage(role="assistant")]

        await sessions.append("session", first)
        await sessions.append("session", second)

        retained = await sessions.get("session")
        self.assertEqual(retained, second)
        self.assertTrue(valid_tool_protocol([ChatMessage(role="system"), *retained]))


if __name__ == "__main__":
    unittest.main()
