from __future__ import annotations

import unittest
from typing import Any, Dict, Sequence

from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.providers import ModelProvider
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class FakeSearchTool(AgentTool):
    def __init__(self) -> None:
        self.arguments: Dict[str, Any] = {}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="test search",
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.arguments = arguments
        return ToolResult(
            success=True,
            code=0,
            message="success",
            data={"list": [{"id": 1001, "title": "测试演唱会"}]},
        )


class ScriptedProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="search_programs",
                        arguments={"keyword": "测试"},
                    )
                ]
            )
        self.assert_tool_observation(messages)
        return ProviderResponse(content="找到：测试演唱会（ID 1001）")

    def assert_tool_observation(self, messages: Sequence[ChatMessage]) -> None:
        tool_message = messages[-1]
        if tool_message.role != "tool" or '"success":true' not in (tool_message.content or ""):
            raise AssertionError("tool observation was not returned to the model")


class AgentRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_tool_observation_model_closed_loop(self) -> None:
        provider = ScriptedProvider()
        tool = FakeSearchTool()
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
        )
        events = []

        result = await runner.run(
            "帮我找测试演唱会", "session-test", lambda event: events.append(event)
        )

        self.assertEqual(result.answer, "找到：测试演唱会（ID 1001）")
        self.assertEqual(result.tool_calls, ["search_programs"])
        self.assertRegex(result.trace_id, r"^[0-9a-f]{32}$")
        self.assertEqual(tool.arguments, {"keyword": "测试"})
        self.assertEqual(provider.calls, 2)
        self.assertEqual(events[0]["type"], "turn.started")
        self.assertEqual(events[0]["traceId"], result.trace_id)
        self.assertEqual(events[-1]["type"], "turn.completed")

    async def test_unknown_tool_is_a_safe_observation(self) -> None:
        registry = ToolRegistry()
        result = await registry.execute(
            "missing_tool",
            {},
            ToolContext("session", "turn", "call", "trace"),
            timeout_seconds=1,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, 404)
        self.assertFalse(result.retryable)


if __name__ == "__main__":
    unittest.main()
