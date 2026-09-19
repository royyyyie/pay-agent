"""Offline release gates for deterministic recommendation constraints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ToolCall
from .recommendation import RecommendationConstraintGuard, parse_price


class RecommendationEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    user_text: str = Field(min_length=1, max_length=2000)
    model_tool_name: str = Field(default="search_programs", min_length=1, max_length=128)
    model_arguments: dict[str, object] = Field(default_factory=dict)
    allowed_tool_names: tuple[str, ...] = ("search_programs", "recommend_programs")
    expected_tool_name: str = Field(default="recommend_programs", min_length=1, max_length=128)
    expected_max_price: Decimal | None = Field(default=None, ge=0)
    expected_preference: str | None = None
    must_require_live_verification: bool = True

    @model_validator(mode="after")
    def validate_tools(self) -> RecommendationEvalCase:
        if self.expected_tool_name not in self.allowed_tool_names:
            raise ValueError("expected recommendation tool must be allowed")
        if len(self.allowed_tool_names) > 32:
            raise ValueError("allowed tool set is too large")
        return self


@dataclass(frozen=True, slots=True)
class RecommendationEvalFailure:
    case_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RecommendationEvalReport:
    total: int
    passed: int
    routing_correct: int
    budget_cases: int
    budgets_enforced: int
    preference_cases: int
    preferences_enforced: int
    verification_cases: int
    verification_enforced: int
    failures: tuple[RecommendationEvalFailure, ...]

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 1.0

    @property
    def routing_accuracy(self) -> float:
        return self._rate(self.routing_correct, self.total)

    @property
    def budget_enforcement_rate(self) -> float:
        return self._rate(self.budgets_enforced, self.budget_cases)

    @property
    def preference_accuracy(self) -> float:
        return self._rate(self.preferences_enforced, self.preference_cases)

    @property
    def live_verification_rate(self) -> float:
        return self._rate(self.verification_enforced, self.verification_cases)

    @property
    def passed_red_lines(self) -> bool:
        return not self.failures and self.live_verification_rate == 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "passed": self.passed,
            "routingAccuracy": round(self.routing_accuracy, 6),
            "budgetEnforcementRate": round(self.budget_enforcement_rate, 6),
            "preferenceAccuracy": round(self.preference_accuracy, 6),
            "liveVerificationRate": round(self.live_verification_rate, 6),
            "passedRedLines": self.passed_red_lines,
            "failures": [
                {"caseId": failure.case_id, "reason": failure.reason} for failure in self.failures
            ],
        }


def evaluate_recommendations(
    cases: Sequence[RecommendationEvalCase],
) -> RecommendationEvalReport:
    if not cases:
        raise ValueError("recommendation evaluation set must not be empty")
    passed = 0
    routing_correct = 0
    budget_cases = 0
    budgets_enforced = 0
    preference_cases = 0
    preferences_enforced = 0
    verification_cases = 0
    verification_enforced = 0
    failures: list[RecommendationEvalFailure] = []

    for case in cases:
        guard = RecommendationConstraintGuard.from_user_text(case.user_text)
        outcome = guard.apply(
            (ToolCall("eval-call", case.model_tool_name, dict(case.model_arguments)),),
            frozenset(case.allowed_tool_names),
        )
        actual = outcome.tool_calls[0]
        reasons: list[str] = []
        if actual.name == case.expected_tool_name:
            routing_correct += 1
        else:
            reasons.append(f"tool is {actual.name}, expected {case.expected_tool_name}")

        if case.expected_max_price is not None:
            budget_cases += 1
            actual_price = parse_price(actual.arguments.get("maxPrice"))
            if actual_price == case.expected_max_price:
                budgets_enforced += 1
            else:
                reasons.append(f"maxPrice is {actual_price}, expected {case.expected_max_price}")

        if case.expected_preference is not None:
            preference_cases += 1
            if actual.arguments.get("preference") == case.expected_preference:
                preferences_enforced += 1
            else:
                reasons.append(
                    "preference is "
                    f"{actual.arguments.get('preference')}, expected {case.expected_preference}"
                )

        if case.must_require_live_verification:
            verification_cases += 1
            if guard.recommendation_intent and actual.name == "recommend_programs":
                verification_enforced += 1
            else:
                reasons.append("live inventory verification was not enforced")

        if reasons:
            failures.append(RecommendationEvalFailure(case.case_id, "; ".join(reasons)))
        else:
            passed += 1

    return RecommendationEvalReport(
        total=len(cases),
        passed=passed,
        routing_correct=routing_correct,
        budget_cases=budget_cases,
        budgets_enforced=budgets_enforced,
        preference_cases=preference_cases,
        preferences_enforced=preferences_enforced,
        verification_cases=verification_cases,
        verification_enforced=verification_enforced,
        failures=tuple(failures),
    )


def load_recommendation_eval_cases(
    path: str | Path,
) -> tuple[RecommendationEvalCase, ...]:
    eval_path = Path(path).resolve()
    try:
        raw = json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("recommendation evaluation set is not valid UTF-8 JSON") from exc
    if not isinstance(raw, list) or not 1 <= len(raw) <= 10_000:
        raise ValueError("recommendation evaluation set must be a bounded nonempty JSON array")
    return tuple(RecommendationEvalCase.model_validate(item) for item in raw)
