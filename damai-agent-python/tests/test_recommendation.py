from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any, Sequence

from damai_agent.generated.tool_models import ProgramSearchRequest
from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.recommendation import RecommendationConstraintGuard, extract_max_price
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class RecommendationConstraintGuardTest(unittest.TestCase):
    def test_extracts_explicit_budget_caps_without_treating_any_price_as_a_cap(self) -> None:
        cases = {
            "帮我找 800 元以内的音乐剧": Decimal("800"),
            "预算为￥399.50": Decimal("399.50"),
            "最多 1000 块": Decimal("1000"),
            "预算 800 元，但不能超过 600 元": Decimal("600"),
            "票价 380 元起": None,
            "预算 1000000000 元": None,
            "预算 399.999 元": None,
            "多少钱": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_max_price(text), expected)

    def test_injects_or_tightens_budget_without_relaxing_model_constraint(self) -> None:
        guard = RecommendationConstraintGuard(Decimal("800"))
        calls, changed = guard.apply(
            (
                ToolCall("a", "search_programs", {"keyword": "音乐剧"}),
                ToolCall("b", "search_programs", {"maxPrice": 1000}),
                ToolCall("c", "search_programs", {"maxPrice": 500}),
                ToolCall("d", "get_program_detail", {"programId": 1}),
            )
        )
        self.assertEqual(changed, 2)
        self.assertEqual(calls[0].arguments["maxPrice"], 800.0)
        self.assertEqual(calls[1].arguments["maxPrice"], 800.0)
        self.assertEqual(calls[2].arguments["maxPrice"], 500)
        self.assertNotIn("maxPrice", calls[3].arguments)

    def test_invalid_model_budget_is_replaced_and_no_budget_is_a_noop(self) -> None:
        call = ToolCall("a", "search_programs", {"maxPrice": "not-a-number"})
        calls, changed = RecommendationConstraintGuard(Decimal("300")).apply((call,))
        self.assertEqual(changed, 1)
        self.assertEqual(calls[0].arguments["maxPrice"], 300.0)
        unchanged, changed = RecommendationConstraintGuard().apply((call,))
        self.assertEqual(unchanged, (call,))
        self.assertEqual(changed, 0)


class RecordingSearchTool(AgentTool):
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="search",
            parameters={"type": "object", "additionalProperties": False},
            request_model=ProgramSearchRequest,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.arguments = arguments
        return ToolResult(success=True, code=0, message="success", data={"list": []})


class TwoRoundRecommendationProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.second_messages: Sequence[ChatMessage] = ()

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[
                    ToolCall(
                        "search-1",
                        "search_programs",
                        {"keyword": "音乐剧", "maxPrice": 1200},
                    )
                ],
                finish_reason="tool_calls",
            )
        self.second_messages = messages
        return ProviderResponse(content="没有符合硬约束的候选。")


class RecommendationRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_clamps_model_tool_arguments_to_user_budget(self) -> None:
        provider = TwoRoundRecommendationProvider()
        tool = RecordingSearchTool()
        runner = AgentRunner(provider, ToolRegistry((tool,)), InMemorySessionStore())

        result = await runner.run("帮我找 800 元以内的音乐剧", "recommendation-budget")

        self.assertIsNone(result.error_code)
        self.assertEqual(tool.arguments["maxPrice"], 800.0)
        assistant_calls = [
            call
            for message in provider.second_messages
            if message.role == "assistant"
            for call in message.tool_calls
        ]
        self.assertEqual(assistant_calls[0].arguments["maxPrice"], 800.0)
