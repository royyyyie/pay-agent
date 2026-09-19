"""Deterministic guards for recommendation hard constraints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import AbstractSet, Sequence

from .models import ToolCall

_MAX_PRICE = Decimal("999999999.99")
_PRICE_PATTERNS = (
    re.compile(
        r"(?:预算(?:是|为|在)?|不超过|不能超过|最多|最高|上限)\s*"
        r"(?:人民币|[¥￥])?\s*(\d{1,9}(?:\.\d{1,2})?)(?![\d.])\s*(?:元|块)?"
    ),
    re.compile(
        r"(?:人民币|[¥￥])?\s*(\d{1,9}(?:\.\d{1,2})?)(?![\d.])\s*"
        r"(?:元|块)\s*(?:以内|以下|封顶|预算)"
    ),
)
_RECOMMENDATION_PATTERN = re.compile(
    r"(?:推荐|哪个好|哪场好|帮我找|替我找|最便宜|价格最低|低价优先|便宜优先|"
    r"最早|尽快|时间优先|余票最多|票最多|库存最多|余量优先|"
    r"(?:比较|排序).{0,10}(?:演出|节目|音乐剧|演唱会))"
)
_PREFERENCE_PATTERNS = (
    ("LOWEST_PRICE", re.compile(r"(?:最便宜|价格最低|低价优先|便宜优先)")),
    ("EARLIEST_SHOW", re.compile(r"(?:最早|尽快|时间优先|尽早)")),
    ("MOST_AVAILABLE", re.compile(r"(?:余票最多|票最多|库存最多|余量优先)")),
)


def extract_max_price(user_text: str) -> Decimal | None:
    candidates: list[Decimal] = []
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(user_text):
            try:
                value = Decimal(match.group(1))
            except InvalidOperation:
                continue
            if Decimal("0") <= value <= _MAX_PRICE:
                candidates.append(value)
    return min(candidates) if candidates else None


def is_recommendation_query(user_text: str) -> bool:
    return _RECOMMENDATION_PATTERN.search(user_text) is not None


def extract_preference(user_text: str) -> str | None:
    for preference, pattern in _PREFERENCE_PATTERNS:
        if pattern.search(user_text):
            return preference
    return None


@dataclass(frozen=True, slots=True)
class RecommendationGuardOutcome:
    tool_calls: tuple[ToolCall, ...]
    changed_calls: int = 0
    applied_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecommendationConstraintGuard:
    max_price: Decimal | None = None
    recommendation_intent: bool = False
    preference: str | None = None

    @classmethod
    def from_user_text(cls, user_text: str) -> RecommendationConstraintGuard:
        return cls(
            max_price=extract_max_price(user_text),
            recommendation_intent=is_recommendation_query(user_text),
            preference=extract_preference(user_text),
        )

    @property
    def active(self) -> bool:
        return (
            self.max_price is not None or self.recommendation_intent or self.preference is not None
        )

    def apply(
        self,
        calls: Sequence[ToolCall],
        allowed_tool_names: AbstractSet[str] = frozenset(),
    ) -> RecommendationGuardOutcome:
        hardened: list[ToolCall] = []
        changed_calls = 0
        applied_types: set[str] = set()
        for call in calls:
            if call.name not in {"search_programs", "recommend_programs"}:
                hardened.append(call)
                continue
            name = call.name
            arguments = dict(call.arguments)
            call_changed = False
            if (
                self.recommendation_intent
                and name == "search_programs"
                and "recommend_programs" in allowed_tool_names
            ):
                name = "recommend_programs"
                call_changed = True
                applied_types.add("liveInventoryRoute")
            if self.max_price is not None:
                existing = self._price(arguments.get("maxPrice"))
                enforced = self.max_price if existing is None else min(existing, self.max_price)
                if existing != enforced:
                    arguments["maxPrice"] = float(enforced)
                    call_changed = True
                    applied_types.add("maxPrice")
            if name == "recommend_programs" and self.preference is not None:
                if arguments.get("preference") != self.preference:
                    arguments["preference"] = self.preference
                    call_changed = True
                    applied_types.add("preference")
            hardened.append(ToolCall(call.id, name, arguments) if call_changed else call)
            if call_changed:
                changed_calls += 1
        return RecommendationGuardOutcome(
            tool_calls=tuple(hardened),
            changed_calls=changed_calls,
            applied_types=tuple(sorted(applied_types)),
        )

    @staticmethod
    def _price(value: object) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
        return parsed if Decimal("0") <= parsed <= _MAX_PRICE and parsed.is_finite() else None
