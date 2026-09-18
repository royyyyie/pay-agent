"""Conservative per-turn model usage and versioned cost accounting."""

from __future__ import annotations

from typing import Mapping, Protocol

from .config import ModelPrice
from .models import AgentErrorCode, ProviderUsage


class TenantQuota(Protocol):
    async def exhausted(self, tenant_id: str, limit_micro_usd: int) -> bool: ...

    async def charge(
        self,
        tenant_id: str,
        turn_id: str,
        round_number: int,
        amount_micro_usd: int,
        limit_micro_usd: int,
    ) -> bool: ...


class TurnBudget:
    def __init__(
        self,
        max_tokens: int,
        max_cost_micro_usd: int,
        prices: Mapping[str, ModelPrice],
    ) -> None:
        if max_tokens < 0 or max_cost_micro_usd < 0:
            raise ValueError("turn budgets must be non-negative")
        if max_cost_micro_usd and not prices:
            raise ValueError("cost budget requires a price catalog")
        self._max_tokens = max_tokens
        self._max_cost = max_cost_micro_usd
        self._prices = prices
        self._usage = ProviderUsage()
        self._cost = 0
        self._cost_known = bool(prices)

    @property
    def cost_micro_usd(self) -> int | None:
        return self._cost if self._cost_known else None

    def record(self, route: str, usage: ProviderUsage) -> AgentErrorCode | None:
        counts = (
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cached_tokens,
            usage.reasoning_tokens,
        )
        if any(count < 0 for count in counts):
            return AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE
        if usage.total_tokens == 0:
            self._cost_known = False
            if self._max_tokens or self._max_cost:
                return AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE

        self._usage = self._usage + usage
        price = self._prices.get(route)
        if price is None:
            self._cost_known = False
            if self._max_cost:
                return AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE
        elif usage.total_tokens > 0:
            amount = (
                usage.prompt_tokens * price.prompt_micro_usd_per_million
                + usage.completion_tokens * price.completion_micro_usd_per_million
            )
            self._cost += (amount + 999_999) // 1_000_000

        if (self._max_tokens and self._usage.total_tokens > self._max_tokens) or (
            self._max_cost and self._cost > self._max_cost
        ):
            return AgentErrorCode.TURN_BUDGET_EXCEEDED
        return None
