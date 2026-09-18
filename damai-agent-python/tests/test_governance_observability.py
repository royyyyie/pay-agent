from __future__ import annotations

import unittest
from typing import Any, Sequence

from damai_agent.config import ModelPrice
from damai_agent.governance import TurnBudget
from damai_agent.models import (
    AgentErrorCode,
    ChatMessage,
    ProviderResponse,
    ProviderUsage,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.observability import RuntimeMetrics
from damai_agent.providers import ProviderError
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class CountingTool(AgentTool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="lookup", description="lookup", parameters={"type": "object"})

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, code=0, message="ok", data={"value": 1})


class TwoRoundProvider:
    route_name = "test/model"

    def __init__(self, *, missing_usage: bool = False) -> None:
        self.calls = 0
        self.missing_usage = missing_usage

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=[ToolCall("call-1", "lookup", {})],
                usage=ProviderUsage()
                if self.missing_usage
                else ProviderUsage(prompt_tokens=10, completion_tokens=2),
                finish_reason="tool_calls",
                model_route=self.route_name,
            )
        return ProviderResponse(
            content="done",
            usage=ProviderUsage(prompt_tokens=8, completion_tokens=3),
            model_route=self.route_name,
        )


class GovernanceTest(unittest.IsolatedAsyncioTestCase):
    def price(self) -> ModelPrice:
        return ModelPrice(
            version="2026-09",
            prompt_micro_usd_per_million=1_000_000,
            completion_micro_usd_per_million=2_000_000,
        )

    async def test_budget_stops_before_tool_execution(self) -> None:
        provider = TwoRoundProvider()
        tool = CountingTool()
        runner = AgentRunner(
            provider,
            ToolRegistry([tool]),
            InMemorySessionStore(),
            max_turn_tokens=5,
            pricing_catalog={"test/model": self.price()},
        )

        result = await runner.run("lookup", "budget-stop")

        self.assertEqual(result.error_code, AgentErrorCode.TURN_BUDGET_EXCEEDED)
        self.assertEqual(result.usage.total_tokens, 12)
        self.assertEqual(result.cost_micro_usd, 14)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(tool.calls, 0)

    async def test_multi_round_cost_is_integer_and_metrics_have_no_payload(self) -> None:
        provider = TwoRoundProvider()
        tool = CountingTool()
        metrics = RuntimeMetrics()
        runner = AgentRunner(
            provider,
            ToolRegistry([tool]),
            InMemorySessionStore(),
            max_turn_tokens=25,
            max_turn_cost_micro_usd=30,
            pricing_catalog={"test/model": self.price()},
            metrics=metrics,
        )

        result = await runner.run("secret-user-query", "private-session")
        rendered = metrics.render_prometheus()

        self.assertEqual(result.answer, "done")
        self.assertEqual(result.usage.total_tokens, 23)
        self.assertEqual(result.cost_micro_usd, 28)
        self.assertEqual(tool.calls, 1)
        self.assertIn('damai_agent_requests_total{kind="model",outcome="success"} 2', rendered)
        self.assertIn('damai_agent_requests_total{kind="tool",outcome="success"} 1', rendered)
        self.assertIn("damai_agent_cost_micro_usd_total 28", rendered)
        self.assertNotIn("private-session", rendered)
        self.assertNotIn("secret-user-query", rendered)

    async def test_missing_price_or_usage_fails_closed_when_budget_is_enabled(self) -> None:
        for missing_usage in (False, True):
            with self.subTest(missing_usage=missing_usage):
                tool = CountingTool()
                provider = TwoRoundProvider(missing_usage=missing_usage)
                prices = {"test/model" if missing_usage else "other/model": self.price()}
                runner = AgentRunner(
                    provider,
                    ToolRegistry([tool]),
                    InMemorySessionStore(),
                    max_turn_cost_micro_usd=10,
                    pricing_catalog=prices,
                )
                result = await runner.run("lookup", f"missing-{missing_usage}")
                self.assertEqual(result.error_code, AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE)
                self.assertEqual(tool.calls, 0)

    async def test_provider_failure_is_counted_without_secret(self) -> None:
        class FailedProvider:
            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                raise ProviderError("secret-upstream-error")

        metrics = RuntimeMetrics()
        runner = AgentRunner(
            FailedProvider(), ToolRegistry(), InMemorySessionStore(), metrics=metrics
        )
        with self.assertRaises(ProviderError):
            await runner.run("private-question", "failed-session")
        rendered = metrics.render_prometheus()
        self.assertIn('damai_agent_requests_total{kind="model",outcome="error"} 1', rendered)
        self.assertIn('damai_agent_requests_total{kind="turn",outcome="error"} 1', rendered)
        self.assertNotIn("secret-upstream-error", rendered)

    def test_budget_requires_valid_usage_and_prices(self) -> None:
        budget = TurnBudget(0, 10, {"test/model": self.price()})
        self.assertEqual(
            budget.record("unpriced/model", ProviderUsage(prompt_tokens=5)),
            AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE,
        )
        self.assertIsNone(budget.cost_micro_usd)
        self.assertEqual(
            TurnBudget(10, 0, {}).record("test/model", ProviderUsage(prompt_tokens=-1)),
            AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE,
        )
