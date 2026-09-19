"""Deterministic guards for recommendation hard constraints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

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


@dataclass(frozen=True, slots=True)
class RecommendationConstraintGuard:
    max_price: Decimal | None = None

    @classmethod
    def from_user_text(cls, user_text: str) -> RecommendationConstraintGuard:
        return cls(max_price=extract_max_price(user_text))

    @property
    def active(self) -> bool:
        return self.max_price is not None

    def apply(self, calls: Sequence[ToolCall]) -> tuple[tuple[ToolCall, ...], int]:
        if self.max_price is None:
            return tuple(calls), 0
        hardened: list[ToolCall] = []
        changed = 0
        for call in calls:
            if call.name != "search_programs":
                hardened.append(call)
                continue
            arguments = dict(call.arguments)
            existing = self._price(arguments.get("maxPrice"))
            enforced = self.max_price if existing is None else min(existing, self.max_price)
            if existing != enforced:
                arguments["maxPrice"] = float(enforced)
                changed += 1
                hardened.append(ToolCall(call.id, call.name, arguments))
            else:
                hardened.append(call)
        return tuple(hardened), changed

    @staticmethod
    def _price(value: object) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            return None
        try:
            parsed = Decimal(str(value))
        except InvalidOperation:
            return None
        return parsed if Decimal("0") <= parsed <= _MAX_PRICE and parsed.is_finite() else None
