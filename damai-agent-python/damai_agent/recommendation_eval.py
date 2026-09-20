"""Offline release gates for deterministic recommendation constraints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
    expected_preference: (
        Literal["RELEVANCE", "LOWEST_PRICE", "EARLIEST_SHOW", "MOST_AVAILABLE"] | None
    ) = None
    must_require_live_verification: bool = True
    category: str = Field(
        default="unspecified",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )
    risk_level: Literal["standard", "safety_critical"] = "standard"
    judgment_reference: str = Field(
        default="",
        max_length=256,
        pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255})?$",
    )

    @model_validator(mode="after")
    def validate_tools(self) -> RecommendationEvalCase:
        if self.expected_tool_name not in self.allowed_tool_names:
            raise ValueError("expected recommendation tool must be allowed")
        if len(self.allowed_tool_names) > 32:
            raise ValueError("allowed tool set is too large")
        return self


class RecommendationEvalBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["damai.recommendation.eval/v1"]
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_team: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    approval_status: Literal["draft", "approved", "rejected"]
    approval_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
    approved_at: AwareDatetime
    reviewer_count: int = Field(ge=2, le=20)
    cases: tuple[RecommendationEvalCase, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_judgments(self) -> RecommendationEvalBundle:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("recommendation Eval case identifiers must be unique")
        normalized = {" ".join(case.user_text.casefold().split()) for case in self.cases}
        if len(normalized) != len(self.cases):
            raise ValueError("recommendation Eval contains duplicate user judgments")
        if len({case.category for case in self.cases}) > 100:
            raise ValueError("recommendation Eval is limited to 100 categories")
        if any(
            case.category == "unspecified" or not case.judgment_reference for case in self.cases
        ):
            raise ValueError(
                "governed recommendation cases require category and judgment reference"
            )
        return self


@dataclass(frozen=True, slots=True)
class RecommendationEvalAsset:
    cases: tuple[RecommendationEvalCase, ...]
    bundle: RecommendationEvalBundle | None = None

    def release_eligible(self, *, minimum_case_count: int, contract_sha256: str) -> bool:
        bundle = self.bundle
        return bool(
            bundle is not None
            and bundle.approval_status == "approved"
            and bundle.approved_at <= datetime.now(timezone.utc)
            and len(self.cases) >= minimum_case_count
            and re.fullmatch(r"[0-9a-f]{64}", contract_sha256)
            and bundle.contract_sha256 == contract_sha256
            and any(case.risk_level == "safety_critical" for case in self.cases)
        )

    def require_release_eligible(self, *, minimum_case_count: int, contract_sha256: str) -> None:
        if not 100 <= minimum_case_count <= 10_000:
            raise ValueError("production recommendation Eval minimum must be between 100 and 10000")
        if not self.release_eligible(
            minimum_case_count=minimum_case_count,
            contract_sha256=contract_sha256,
        ):
            raise ValueError("production recommendation Eval is not release eligible")

    def governance_payload(
        self,
        *,
        required: bool,
        minimum_case_count: int,
        contract_sha256: str,
    ) -> dict[str, object]:
        categories: dict[str, int] = {}
        risks: dict[str, int] = {}
        judged = 0
        for case in self.cases:
            categories[case.category] = categories.get(case.category, 0) + 1
            risks[case.risk_level] = risks.get(case.risk_level, 0) + 1
            judged += bool(case.judgment_reference)
        bundle = self.bundle
        return {
            "required": required,
            "schemaVersion": (bundle.schema_version if bundle is not None else "legacy-array"),
            "datasetId": bundle.dataset_id if bundle is not None else None,
            "datasetVersion": bundle.dataset_version if bundle is not None else None,
            "ownerTeam": bundle.owner_team if bundle is not None else None,
            "approved": bundle is not None and bundle.approval_status == "approved",
            "approvalReference": bundle.approval_reference if bundle is not None else None,
            "approvedAt": (
                bundle.approved_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if bundle is not None
                else None
            ),
            "reviewerCount": bundle.reviewer_count if bundle is not None else 0,
            "contractSha256": bundle.contract_sha256 if bundle is not None else None,
            "contractMatchVerified": (
                bundle is not None and bundle.contract_sha256 == contract_sha256
            ),
            "caseCount": len(self.cases),
            "minimumCaseCount": minimum_case_count,
            "categoryCounts": dict(sorted(categories.items())),
            "riskLevelCounts": dict(sorted(risks.items())),
            "judgmentCoverage": round(judged / len(self.cases), 6),
            "releaseEligible": self.release_eligible(
                minimum_case_count=minimum_case_count,
                contract_sha256=contract_sha256,
            ),
        }


@dataclass(frozen=True, slots=True)
class RecommendationEvalFailure:
    case_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RecommendationEvalObservation:
    case_id: str
    category: str
    risk_level: str
    passed: bool
    routing_correct: bool
    budget_enforced: bool | None
    preference_enforced: bool | None
    verification_enforced: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "passed": self.passed,
            "routingCorrect": self.routing_correct,
            "budgetEnforced": self.budget_enforced,
            "preferenceEnforced": self.preference_enforced,
            "liveVerificationEnforced": self.verification_enforced,
        }


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
    observations: tuple[RecommendationEvalObservation, ...]

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
            "slices": {
                "category": self._slices("category"),
                "riskLevel": self._slices("risk_level"),
            },
            "caseResults": [observation.to_dict() for observation in self.observations],
        }

    def _slices(self, dimension: Literal["category", "risk_level"]) -> dict[str, object]:
        grouped: dict[str, list[RecommendationEvalObservation]] = {}
        for observation in self.observations:
            grouped.setdefault(getattr(observation, dimension), []).append(observation)
        return {
            key: _recommendation_slice(tuple(observations))
            for key, observations in sorted(grouped.items())
        }


def _recommendation_slice(
    observations: Sequence[RecommendationEvalObservation],
) -> dict[str, object]:
    def rate(values: tuple[bool, ...]) -> float:
        return sum(values) / len(values) if values else 1.0

    budgets = tuple(
        item.budget_enforced for item in observations if item.budget_enforced is not None
    )
    preferences = tuple(
        item.preference_enforced for item in observations if item.preference_enforced is not None
    )
    verification = tuple(
        item.verification_enforced
        for item in observations
        if item.verification_enforced is not None
    )
    return {
        "total": len(observations),
        "passed": sum(item.passed for item in observations),
        "routingAccuracy": round(rate(tuple(item.routing_correct for item in observations)), 6),
        "budgetEnforcementRate": round(rate(budgets), 6),
        "preferenceAccuracy": round(rate(preferences), 6),
        "liveVerificationRate": round(rate(verification), 6),
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
    observations: list[RecommendationEvalObservation] = []

    for case in cases:
        guard = RecommendationConstraintGuard.from_user_text(case.user_text)
        outcome = guard.apply(
            (ToolCall("eval-call", case.model_tool_name, dict(case.model_arguments)),),
            frozenset(case.allowed_tool_names),
        )
        actual = outcome.tool_calls[0]
        reasons: list[str] = []
        routing_ok = actual.name == case.expected_tool_name
        if routing_ok:
            routing_correct += 1
        else:
            reasons.append(f"tool is {actual.name}, expected {case.expected_tool_name}")

        budget_ok: bool | None = None
        if case.expected_max_price is not None:
            budget_cases += 1
            actual_price = parse_price(actual.arguments.get("maxPrice"))
            budget_ok = actual_price == case.expected_max_price
            if budget_ok:
                budgets_enforced += 1
            else:
                reasons.append(f"maxPrice is {actual_price}, expected {case.expected_max_price}")

        preference_ok: bool | None = None
        if case.expected_preference is not None:
            preference_cases += 1
            preference_ok = actual.arguments.get("preference") == case.expected_preference
            if preference_ok:
                preferences_enforced += 1
            else:
                reasons.append(
                    "preference is "
                    f"{actual.arguments.get('preference')}, expected {case.expected_preference}"
                )

        verification_ok: bool | None = None
        if case.must_require_live_verification:
            verification_cases += 1
            verification_ok = guard.recommendation_intent and actual.name == "recommend_programs"
            if verification_ok:
                verification_enforced += 1
            else:
                reasons.append("live inventory verification was not enforced")

        if reasons:
            failures.append(RecommendationEvalFailure(case.case_id, "; ".join(reasons)))
        else:
            passed += 1
        observations.append(
            RecommendationEvalObservation(
                case_id=case.case_id,
                category=case.category,
                risk_level=case.risk_level,
                passed=not reasons,
                routing_correct=routing_ok,
                budget_enforced=budget_ok,
                preference_enforced=preference_ok,
                verification_enforced=verification_ok,
            )
        )

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
        observations=tuple(observations),
    )


def load_recommendation_eval_asset(path: str | Path) -> RecommendationEvalAsset:
    eval_path = Path(path).resolve()
    if not eval_path.is_file() or eval_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("recommendation evaluation set must be a bounded JSON file")
    try:
        raw = json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("recommendation evaluation set is not valid UTF-8 JSON") from exc
    if isinstance(raw, list):
        if not 1 <= len(raw) <= 10_000:
            raise ValueError("recommendation evaluation set must be bounded and nonempty")
        return RecommendationEvalAsset(
            cases=tuple(RecommendationEvalCase.model_validate(item) for item in raw)
        )
    if not isinstance(raw, dict):
        raise ValueError("recommendation evaluation set must be a legacy array or bundle")
    bundle = RecommendationEvalBundle.model_validate(raw)
    return RecommendationEvalAsset(cases=bundle.cases, bundle=bundle)


def load_recommendation_eval_cases(
    path: str | Path,
) -> tuple[RecommendationEvalCase, ...]:
    return load_recommendation_eval_asset(path).cases
