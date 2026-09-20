"""Paired, auditable comparison for production RAG experiments."""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ComparisonMode = Literal["non_inferiority", "superiority"]
RetrievalProfile = Literal["lexical", "semantic_hybrid", "semantic_rerank"]


class RagHumanReviewEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["damai.rag.human-review/v1"]
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    baseline_profile: RetrievalProfile
    candidate_profile: RetrievalProfile
    review_protocol_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    blind_review: bool
    judged_case_count: int = Field(ge=100, le=10_000)
    reviewer_count: int = Field(ge=2, le=20)
    agreement_rate: float = Field(ge=0.8, le=1.0)
    safety_approved: bool
    approval_status: Literal["draft", "approved", "rejected"]
    approval_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
    approved_at: AwareDatetime


def _bounded_number(payload: dict[str, object], key: str, *, low: float, high: float) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"RAG experiment metric {key} is invalid")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"RAG experiment metric {key} is invalid")
    return result


def _case_results(report: dict[str, object]) -> dict[str, tuple[bool, float]]:
    quality = report.get("quality")
    governance = report.get("evalGovernance")
    if not isinstance(quality, dict) or quality.get("passed") is not True:
        raise ValueError("RAG experiment requires a passing quality report")
    if quality.get("failures") != []:
        raise ValueError("RAG experiment report contains quality failures")
    if not isinstance(governance, dict) or governance.get("releaseEligible") is not True:
        raise ValueError("RAG experiment requires release-eligible Eval governance")
    case_count = governance.get("caseCount")
    raw_results = quality.get("caseResults")
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count < 100
        or not isinstance(raw_results, list)
        or len(raw_results) != case_count
    ):
        raise ValueError("RAG experiment case evidence is incomplete")
    results: dict[str, tuple[bool, float]] = {}
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("RAG experiment case evidence is invalid")
        case_id = raw.get("caseId")
        passed = raw.get("passed")
        utility = raw.get("utilityScore")
        if (
            not isinstance(case_id, str)
            or not case_id
            or len(case_id) > 128
            or not isinstance(passed, bool)
            or isinstance(utility, bool)
            or not isinstance(utility, (int, float))
            or not math.isfinite(float(utility))
            or not 0 <= float(utility) <= 1
            or case_id in results
        ):
            raise ValueError("RAG experiment case evidence is invalid")
        results[case_id] = (passed, float(utility))
    return results


def _exact_one_sided_sign_p_value(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0 or wins <= losses:
        return 1.0
    numerator: int = sum(math.comb(discordant, value) for value in range(wins, discordant + 1))
    return float(numerator / (2**discordant))


def _bootstrap_lower_bound(
    deltas: tuple[float, ...],
    *,
    confidence: float,
    samples: int,
    seed_material: str,
) -> float:
    seed = int(hashlib.sha256(seed_material.encode("ascii")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    size = len(deltas)
    means = sorted(
        sum(deltas[rng.randrange(size)] for _ in range(size)) / size for _ in range(samples)
    )
    index = max(0, math.floor((1 - confidence) * samples) - 1)
    return means[index]


def compare_rag_experiment(
    baseline: dict[str, object],
    candidate: dict[str, object],
    review: RagHumanReviewEvidence,
    *,
    baseline_sha256: str,
    candidate_sha256: str,
    review_sha256: str,
    mode: ComparisonMode = "non_inferiority",
    margin: float = 0.02,
    confidence: float = 0.95,
    bootstrap_samples: int = 5_000,
) -> dict[str, object]:
    if not 0 <= margin <= 0.2:
        raise ValueError("RAG experiment margin must be between 0 and 0.2")
    if not 0.9 <= confidence <= 0.999:
        raise ValueError("RAG experiment confidence must be between 0.9 and 0.999")
    if not 1_000 <= bootstrap_samples <= 50_000:
        raise ValueError("RAG experiment bootstrap samples must be between 1000 and 50000")
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in (baseline_sha256, candidate_sha256, review_sha256)
    ):
        raise ValueError("RAG experiment evidence hashes are invalid")
    for report in (baseline, candidate):
        if report.get("schemaVersion") != "damai.rag.acceptance/v1":
            raise ValueError("RAG experiment report schema is invalid")
    if baseline.get("targetIndex") != candidate.get("targetIndex") or baseline.get(
        "evalSetSha256"
    ) != candidate.get("evalSetSha256"):
        raise ValueError("RAG experiment reports do not target the same index and Eval set")
    baseline_profile = baseline.get("retrievalProfile")
    candidate_profile = candidate.get("retrievalProfile")
    if (
        baseline_profile != review.baseline_profile
        or candidate_profile != review.candidate_profile
        or baseline_profile == candidate_profile
    ):
        raise ValueError("RAG experiment profiles do not match human review evidence")
    baseline_governance = baseline.get("evalGovernance")
    candidate_governance = candidate.get("evalGovernance")
    if not isinstance(baseline_governance, dict) or not isinstance(candidate_governance, dict):
        raise ValueError("RAG experiment Eval governance is missing")
    identity_keys = ("datasetId", "datasetVersion", "catalogSha256", "caseCount")
    if any(baseline_governance.get(key) != candidate_governance.get(key) for key in identity_keys):
        raise ValueError("RAG experiment reports use different governed datasets")
    if (
        review.dataset_id != candidate_governance.get("datasetId")
        or review.dataset_version != candidate_governance.get("datasetVersion")
        or review.judged_case_count != candidate_governance.get("caseCount")
        or not review.blind_review
        or not review.safety_approved
        or review.approval_status != "approved"
        or review.approved_at > datetime.now(timezone.utc)
    ):
        raise ValueError("RAG experiment human review evidence is not release eligible")

    baseline_cases = _case_results(baseline)
    candidate_cases = _case_results(candidate)
    if set(baseline_cases) != set(candidate_cases):
        raise ValueError("RAG experiment reports do not contain the same cases")
    ordered_ids = sorted(baseline_cases)
    deltas = tuple(candidate_cases[key][1] - baseline_cases[key][1] for key in ordered_ids)
    red_line_regressions = sum(
        baseline_cases[key][0] and not candidate_cases[key][0] for key in ordered_ids
    )
    wins = sum(delta > 1e-9 for delta in deltas)
    losses = sum(delta < -1e-9 for delta in deltas)
    ties = len(deltas) - wins - losses
    mean_delta = sum(deltas) / len(deltas)
    lower_bound = _bootstrap_lower_bound(
        deltas,
        confidence=confidence,
        samples=bootstrap_samples,
        seed_material=baseline_sha256 + candidate_sha256,
    )
    sign_p_value = _exact_one_sided_sign_p_value(wins, losses)
    statistics_passed = red_line_regressions == 0 and (
        lower_bound >= -margin
        if mode == "non_inferiority"
        else lower_bound > 0 and sign_p_value <= 1 - confidence
    )
    return {
        "schemaVersion": "damai.rag.experiment/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "targetIndex": candidate.get("targetIndex"),
        "evalSetSha256": candidate.get("evalSetSha256"),
        "datasetId": review.dataset_id,
        "datasetVersion": review.dataset_version,
        "baselineProfile": baseline_profile,
        "candidateProfile": candidate_profile,
        "baselineReportSha256": baseline_sha256,
        "candidateReportSha256": candidate_sha256,
        "reviewEvidenceSha256": review_sha256,
        "statistics": {
            "mode": mode,
            "caseCount": len(deltas),
            "meanUtilityDelta": round(mean_delta, 6),
            "lowerConfidenceBound": round(lower_bound, 6),
            "confidence": confidence,
            "nonInferiorityMargin": margin,
            "bootstrapSamples": bootstrap_samples,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "exactOneSidedSignPValue": round(sign_p_value, 8),
            "redLineRegressions": red_line_regressions,
            "passed": statistics_passed,
        },
        "humanReview": {
            "blind": review.blind_review,
            "reviewProtocolVersion": review.review_protocol_version,
            "judgedCaseCount": review.judged_case_count,
            "reviewerCount": review.reviewer_count,
            "agreementRate": review.agreement_rate,
            "safetyApproved": review.safety_approved,
            "approvalReference": review.approval_reference,
            "approvedAt": review.approved_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "passed": True,
        },
        "passed": statistics_passed,
    }
