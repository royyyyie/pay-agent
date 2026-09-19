from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any, Sequence

from damai_agent.generated.tool_models import ProgramRecommendationRequest, ProgramSearchRequest
from damai_agent.models import (
    AgentErrorCode,
    ChatMessage,
    ProviderResponse,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.recommendation import (
    RecommendationConstraintGuard,
    extract_max_price,
    extract_preference,
    is_recommendation_query,
)
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
        outcome = guard.apply(
            (
                ToolCall("a", "search_programs", {"keyword": "音乐剧"}),
                ToolCall("b", "search_programs", {"maxPrice": 1000}),
                ToolCall("c", "search_programs", {"maxPrice": 500}),
                ToolCall("d", "get_program_detail", {"programId": 1}),
            )
        )
        self.assertEqual(outcome.changed_calls, 2)
        self.assertEqual(outcome.applied_types, ("maxPrice",))
        self.assertEqual(outcome.tool_calls[0].arguments["maxPrice"], 800.0)
        self.assertEqual(outcome.tool_calls[1].arguments["maxPrice"], 800.0)
        self.assertEqual(outcome.tool_calls[2].arguments["maxPrice"], 500)
        self.assertNotIn("maxPrice", outcome.tool_calls[3].arguments)

    def test_invalid_model_budget_is_replaced_and_no_budget_is_a_noop(self) -> None:
        call = ToolCall("a", "search_programs", {"maxPrice": "not-a-number"})
        outcome = RecommendationConstraintGuard(Decimal("300")).apply((call,))
        self.assertEqual(outcome.changed_calls, 1)
        self.assertEqual(outcome.tool_calls[0].arguments["maxPrice"], 300.0)
        unchanged = RecommendationConstraintGuard().apply((call,))
        self.assertEqual(unchanged.tool_calls, (call,))
        self.assertEqual(unchanged.changed_calls, 0)

    def test_detects_recommendation_intent_and_explicit_soft_preference(self) -> None:
        self.assertTrue(is_recommendation_query("推荐几个适合周末看的音乐剧"))
        self.assertTrue(is_recommendation_query("帮我找余票最多的演唱会"))
        self.assertFalse(is_recommendation_query("查询节目 1001 的详情"))
        self.assertFalse(is_recommendation_query("请优先说明退票规则"))
        self.assertEqual(extract_preference("优先推荐最便宜的"), "LOWEST_PRICE")
        self.assertEqual(extract_preference("我想看时间最早的"), "EARLIEST_SHOW")
        self.assertEqual(extract_preference("余票最多的优先"), "MOST_AVAILABLE")

    def test_routes_recommendation_search_to_live_inventory_tool(self) -> None:
        guard = RecommendationConstraintGuard.from_user_text("帮我找 800 元以内最便宜的音乐剧")
        outcome = guard.apply(
            (ToolCall("a", "search_programs", {"keyword": "音乐剧"}),),
            {"search_programs", "recommend_programs"},
        )
        self.assertEqual(outcome.changed_calls, 1)
        self.assertEqual(outcome.tool_calls[0].name, "recommend_programs")
        self.assertEqual(outcome.tool_calls[0].arguments["maxPrice"], 800.0)
        self.assertEqual(outcome.tool_calls[0].arguments["preference"], "LOWEST_PRICE")
        self.assertEqual(
            outcome.applied_types,
            ("liveInventoryRoute", "maxPrice", "preference"),
        )


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


class RecordingRecommendationTool(AgentTool):
    def __init__(self) -> None:
        self.arguments: dict[str, Any] = {}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="recommend_programs",
            description="recommend with live inventory",
            parameters={"type": "object", "additionalProperties": False},
            request_model=ProgramRecommendationRequest,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.arguments = arguments
        return ToolResult(
            success=True,
            code=0,
            message="success",
            data={
                "scannedCount": 0,
                "eligibleCount": 0,
                "preference": arguments["preference"],
                "list": [],
            },
        )


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


class UnverifiedRecommendationProvider:
    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        return ProviderResponse(content="我直接推荐一个未核验库存的节目。")


class RecommendationRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_clamps_model_tool_arguments_to_user_budget(self) -> None:
        provider = TwoRoundRecommendationProvider()
        search_tool = RecordingSearchTool()
        recommendation_tool = RecordingRecommendationTool()
        runner = AgentRunner(
            provider,
            ToolRegistry((search_tool, recommendation_tool)),
            InMemorySessionStore(),
        )

        result = await runner.run("帮我找 800 元以内最便宜的音乐剧", "recommendation-budget")

        self.assertIsNone(result.error_code)
        self.assertEqual(search_tool.arguments, {})
        self.assertEqual(recommendation_tool.arguments["maxPrice"], 800.0)
        self.assertEqual(recommendation_tool.arguments["preference"], "LOWEST_PRICE")
        assistant_calls = [
            call
            for message in provider.second_messages
            if message.role == "assistant"
            for call in message.tool_calls
        ]
        self.assertEqual(assistant_calls[0].name, "recommend_programs")
        self.assertEqual(assistant_calls[0].arguments["maxPrice"], 800.0)

    async def test_runner_fails_closed_when_recommendation_inventory_was_not_verified(self) -> None:
        runner = AgentRunner(
            UnverifiedRecommendationProvider(),
            ToolRegistry((RecordingRecommendationTool(),)),
            InMemorySessionStore(),
        )

        result = await runner.run("推荐几场音乐剧", "recommendation-unverified")

        self.assertEqual(
            result.error_code,
            AgentErrorCode.RECOMMENDATION_VERIFICATION_REQUIRED,
        )
        self.assertIn("实时余票核验", result.final_content)
