from __future__ import annotations

import asyncio
import unittest
from typing import Any, Dict, Sequence

from damai_agent.models import (
    AgentErrorCode,
    ChatMessage,
    ProviderResponse,
    ProviderUsage,
    TicketTurnContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from damai_agent.providers import ModelProvider
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class FakeSearchTool(AgentTool):
    def __init__(self) -> None:
        self.arguments: Dict[str, Any] = {}
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="test search",
            parameters={"type": "object", "properties": {}},
            required_scope="programs:read",
        )

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
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
                ],
                usage=ProviderUsage(prompt_tokens=10, completion_tokens=3),
                finish_reason="tool_calls",
                model_route="test/model-a",
            )
        self.assert_tool_observation(messages)
        return ProviderResponse(
            content="找到：测试演唱会（ID 1001）",
            usage=ProviderUsage(prompt_tokens=20, completion_tokens=4),
            model_route="test/model-a",
        )

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
        self.assertEqual(result.usage.prompt_tokens, 30)
        self.assertEqual(result.usage.completion_tokens, 7)
        self.assertEqual(result.model_route, "test/model-a")
        self.assertEqual(result.stop_reason, "stop")
        self.assertGreaterEqual(len(result.messages), 4)
        self.assertEqual(events[0]["type"], "turn.started")
        self.assertEqual(events[0]["traceId"], result.trace_id)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(
            [event["eventSeq"] for event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertTrue(all(event["traceId"] == result.trace_id for event in events))

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

    async def test_same_session_turns_are_serialized(self) -> None:
        class SlowProvider:
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0

            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.02)
                self.active -= 1
                return ProviderResponse(content="ok")

        provider = SlowProvider()
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(),
            sessions=InMemorySessionStore(),
        )

        await asyncio.gather(
            runner.run("first", "session-serialized"),
            runner.run("second", "session-serialized"),
        )

        self.assertEqual(provider.max_active, 1)

    async def test_refused_response_cannot_smuggle_a_tool_call(self) -> None:
        class RefusingProvider:
            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                return ProviderResponse(
                    content="unsafe",
                    tool_calls=[
                        ToolCall(
                            id="smuggled-call",
                            name="search_programs",
                            arguments={},
                        )
                    ],
                    finish_reason="content_filter",
                    refusal="blocked",
                )

        tool = FakeSearchTool()
        runner = AgentRunner(
            provider=RefusingProvider(),
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
        )

        result = await runner.run("unsafe request", "session-refused")

        self.assertEqual(result.error_code, AgentErrorCode.PROVIDER_FINISH_REJECTED)
        self.assertEqual(tool.calls, 0)
        self.assertNotIn("unsafe", result.final_content)

    async def test_unauthorized_tools_are_not_exposed_to_provider(self) -> None:
        class CapturingProvider:
            def __init__(self) -> None:
                self.tool_names: list[str] = []

            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                self.tool_names = [tool.name for tool in tools]
                return ProviderResponse(content="no tools")

        provider = CapturingProvider()
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([FakeSearchTool()]),
            sessions=InMemorySessionStore(),
        )
        trusted_context = TicketTurnContext(
            tenant_id="tenant-1",
            user_id="user-1",
            session_key="session-no-scope",
            turn_id="turn-no-scope",
            request_id="request-no-scope",
            trace_id="b" * 32,
            locale="zh-CN",
            channel="test",
            tool_scopes=frozenset(),
            risk_ceiling=ToolRisk.READ_ONLY,
            delegation_token_id="delegation-1",
        )

        await runner.run(
            "hello",
            "session-no-scope",
            trusted_context=trusted_context,
        )

        self.assertEqual(provider.tool_names, [])


if __name__ == "__main__":
    unittest.main()
