"""Final phase 4 promotion evidence verification."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_REPORT_BYTES = 8 * 1024 * 1024


def _read_report(path: Path | None, *, label: str) -> tuple[dict[str, object], str]:
    if path is None or not path.is_file() or not 0 < path.stat().st_size <= _MAX_REPORT_BYTES:
        raise ValueError(f"a bounded {label} is required for phase 4 promotion")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _number(payload: dict[str, object], key: str, *, minimum: float, maximum: float) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"phase 4 evidence metric {key} is invalid")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"phase 4 evidence metric {key} is invalid")
    return result


def _fresh_timestamp(
    payload: dict[str, object],
    key: str,
    *,
    now: datetime,
    max_age_hours: int,
) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"phase 4 evidence timestamp {key} is missing")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"phase 4 evidence timestamp {key} is invalid") from exc
    if (
        observed.tzinfo is None
        or observed > now + timedelta(minutes=5)
        or observed < now - timedelta(hours=max_age_hours)
    ):
        raise ValueError(f"phase 4 evidence timestamp {key} is expired or from the future")
    return observed


def _verify_experiment(
    report: dict[str, object],
    acceptance: dict[str, object],
    *,
    index_name: str,
    now: datetime,
    max_age_hours: int,
) -> None:
    governance = acceptance.get("evalGovernance")
    statistics = report.get("statistics")
    review = report.get("humanReview")
    if (
        not isinstance(governance, dict)
        or not isinstance(statistics, dict)
        or not isinstance(review, dict)
    ):
        raise ValueError("RAG experiment evidence is incomplete")
    review_reviewer_count = review.get("reviewerCount")
    if (
        report.get("schemaVersion") != "damai.rag.experiment/v1"
        or report.get("passed") is not True
        or report.get("targetIndex") != index_name
        or report.get("evalSetSha256") != acceptance.get("evalSetSha256")
        or report.get("candidateProfile") != acceptance.get("retrievalProfile")
        or report.get("candidateReportSha256") != acceptance.get("benchmarkReportSha256")
        or report.get("datasetId") != governance.get("datasetId")
        or report.get("datasetVersion") != governance.get("datasetVersion")
        or statistics.get("passed") is not True
        or statistics.get("redLineRegressions") != 0
        or statistics.get("caseCount") != governance.get("caseCount")
        or review.get("passed") is not True
        or review.get("blind") is not True
        or review.get("safetyApproved") is not True
        or review.get("judgedCaseCount") != governance.get("caseCount")
        or isinstance(review_reviewer_count, bool)
        or not isinstance(review_reviewer_count, int)
        or review_reviewer_count < 2
        or not isinstance(review.get("approvalReference"), str)
        or _number(review, "agreementRate", minimum=0.8, maximum=1.0) < 0.8
    ):
        raise ValueError("RAG experiment or human review evidence failed verification")
    mode = statistics.get("mode")
    lower = _number(statistics, "lowerConfidenceBound", minimum=-1, maximum=1)
    margin = _number(statistics, "nonInferiorityMargin", minimum=0, maximum=0.2)
    confidence = _number(statistics, "confidence", minimum=0.9, maximum=0.999)
    p_value = _number(statistics, "exactOneSidedSignPValue", minimum=0, maximum=1)
    if mode == "non_inferiority":
        if lower < -margin:
            raise ValueError("RAG non-inferiority evidence failed verification")
    elif mode == "superiority":
        if lower <= 0 or p_value > 1 - confidence:
            raise ValueError("RAG superiority evidence failed verification")
    else:
        raise ValueError("RAG experiment mode is invalid")
    for key in ("baselineReportSha256", "candidateReportSha256", "reviewEvidenceSha256"):
        value = report.get(key)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError("RAG experiment evidence hashes are invalid")
    _fresh_timestamp(report, "generatedAt", now=now, max_age_hours=max_age_hours)
    _fresh_timestamp(review, "approvedAt", now=now, max_age_hours=max_age_hours)


def _verify_recommendation(report: dict[str, object], *, now: datetime, max_age_hours: int) -> None:
    governance = report.get("evalGovernance")
    quality = report.get("quality")
    if not isinstance(governance, dict) or not isinstance(quality, dict):
        raise ValueError("recommendation acceptance evidence is incomplete")
    category_counts = governance.get("categoryCounts")
    risk_counts = governance.get("riskLevelCounts")
    cases = quality.get("caseResults")
    reviewer_count = governance.get("reviewerCount")
    if (
        report.get("schemaVersion") != "damai.recommendation.acceptance/v1"
        or report.get("passed") is not True
        or governance.get("required") is not True
        or governance.get("schemaVersion") != "damai.recommendation.eval/v1"
        or governance.get("approved") is not True
        or governance.get("contractMatchVerified") is not True
        or governance.get("releaseEligible") is not True
        or governance.get("judgmentCoverage") != 1.0
        or governance.get("contractSha256") != report.get("contractSha256")
        or isinstance(reviewer_count, bool)
        or not isinstance(reviewer_count, int)
        or reviewer_count < 2
        or not isinstance(governance.get("approvalReference"), str)
        or not isinstance(governance.get("caseCount"), int)
        or governance.get("caseCount", 0) < 100
        or not isinstance(category_counts, dict)
        or "unspecified" in category_counts
        or not isinstance(risk_counts, dict)
        or risk_counts.get("safety_critical", 0) < 1
        or quality.get("passedRedLines") is not True
        or quality.get("failures") != []
        or any(
            _number(quality, key, minimum=0, maximum=1) != 1.0
            for key in (
                "routingAccuracy",
                "budgetEnforcementRate",
                "preferenceAccuracy",
                "liveVerificationRate",
            )
        )
        or not isinstance(cases, list)
        or len(cases) != governance.get("caseCount")
    ):
        raise ValueError("recommendation acceptance evidence failed verification")
    _fresh_timestamp(report, "generatedAt", now=now, max_age_hours=max_age_hours)
    _fresh_timestamp(governance, "approvedAt", now=now, max_age_hours=max_age_hours)


def _verify_slo(
    report: dict[str, object],
    acceptance: dict[str, object],
    *,
    now: datetime,
    max_age_hours: int,
) -> None:
    faults = report.get("faultScenarios")
    hashes = report.get("evidenceSha256")
    reviewer_count = report.get("reviewerCount")
    required_faults = {"provider", "java_gateway", "redis", "postgresql"}
    if (
        report.get("schemaVersion") != "damai.phase4.slo-attestation/v1"
        or report.get("passed") is not True
        or report.get("knowledgeIndexVersion") != acceptance.get("indexVersion")
        or report.get("environmentTier") not in {"test", "staging"}
        or _number(report, "requestCount", minimum=100, maximum=100_000_000) < 100
        or _number(report, "availability", minimum=0.99, maximum=1) < 0.99
        or _number(report, "p95TurnLatencyMs", minimum=0, maximum=10_000) > 10_000
        or _number(report, "traceSampleCount", minimum=20, maximum=1_000_000) < 20
        or _number(report, "traceContinuityRate", minimum=1, maximum=1) != 1
        or _number(report, "ragJavaTraceSampleCount", minimum=10, maximum=1_000_000) < 10
        or report.get("rollbackDrillPassed") is not True
        or report.get("operationsApproved") is not True
        or report.get("securityApproved") is not True
        or isinstance(reviewer_count, bool)
        or not isinstance(reviewer_count, int)
        or reviewer_count < 2
        or not isinstance(report.get("approvalReference"), str)
        or not isinstance(faults, dict)
        or set(faults) != required_faults
        or not all(
            isinstance(value, dict)
            and value.get("passed") is True
            and value.get("duplicateSideEffects") == 0
            for value in faults.values()
        )
        or not isinstance(hashes, dict)
        or set(hashes) != {"manifest", "metrics", "traces", "faults"}
        or not all(isinstance(value, str) and _SHA256.fullmatch(value) for value in hashes.values())
    ):
        raise ValueError("phase 4 SLO, trace, fault, or rollback evidence failed verification")
    _fresh_timestamp(report, "generatedAt", now=now, max_age_hours=max_age_hours)
    approved = _fresh_timestamp(report, "approvedAt", now=now, max_age_hours=max_age_hours)
    start = _fresh_timestamp(report, "windowStart", now=now, max_age_hours=max_age_hours)
    end = _fresh_timestamp(report, "windowEnd", now=now, max_age_hours=max_age_hours)
    if end - start < timedelta(minutes=10):
        raise ValueError("phase 4 SLO measurement window is too short")
    if approved < end:
        raise ValueError("phase 4 SLO approval predates the measurement window")


def verify_phase4_release_evidence(
    *,
    acceptance_report: Path,
    experiment_report: Path | None,
    recommendation_report: Path | None,
    slo_report: Path | None,
    index_name: str,
    max_age_hours: int,
) -> dict[str, str]:
    if not 1 <= max_age_hours <= 168:
        raise ValueError("phase 4 evidence maximum age must be between 1 and 168 hours")
    acceptance, acceptance_hash = _read_report(acceptance_report, label="acceptance report")
    experiment, experiment_hash = _read_report(experiment_report, label="experiment report")
    recommendation, recommendation_hash = _read_report(
        recommendation_report, label="recommendation report"
    )
    slo, slo_hash = _read_report(slo_report, label="SLO report")
    if acceptance.get("targetIndex") != index_name:
        raise ValueError("phase 4 acceptance report targets a different index")
    now = datetime.now(timezone.utc)
    _verify_experiment(
        experiment,
        acceptance,
        index_name=index_name,
        now=now,
        max_age_hours=max_age_hours,
    )
    _verify_recommendation(recommendation, now=now, max_age_hours=max_age_hours)
    _verify_slo(slo, acceptance, now=now, max_age_hours=max_age_hours)
    return {
        "acceptanceReportSha256": acceptance_hash,
        "experimentReportSha256": experiment_hash,
        "recommendationReportSha256": recommendation_hash,
        "sloReportSha256": slo_hash,
    }
