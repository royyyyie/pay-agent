"""Offline retrieval and dynamic-fact red-line evaluation."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .rag import StableKnowledgeRag


class RagEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    query: str = Field(min_length=1, max_length=2000)
    tenant_id: str = Field(min_length=1, max_length=128)
    locale: str = Field(default="zh-CN", min_length=2, max_length=35)
    expected_document_ids: tuple[str, ...] = ()
    expected_top_document_id: str | None = None
    forbidden_document_ids: tuple[str, ...] = ()
    must_block_as_dynamic: bool = False
    category: str = Field(
        default="unspecified",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    risk_level: Literal["standard", "safety_critical"] = "standard"
    judgment_reference: str = Field(
        default="",
        max_length=256,
        pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255})?$",
    )

    @model_validator(mode="after")
    def validate_expectation(self) -> RagEvalCase:
        if self.must_block_as_dynamic == bool(self.expected_document_ids):
            raise ValueError("eval case must define exactly one expected outcome")
        if self.expected_top_document_id is not None and (
            self.expected_top_document_id not in self.expected_document_ids
        ):
            raise ValueError("expected top document must be one of the relevant documents")
        if set(self.expected_document_ids) & set(self.forbidden_document_ids):
            raise ValueError("relevant and forbidden documents must not overlap")
        return self


class RagEvalBundle(BaseModel):
    """Auditable, business-approved relevance judgments for a catalog release."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["damai.rag.eval/v1"]
    dataset_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    dataset_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_team: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    approval_status: Literal["draft", "approved", "rejected"]
    approval_reference: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]*$",
    )
    approved_at: AwareDatetime
    reviewer_count: int = Field(ge=2, le=20)
    cases: tuple[RagEvalCase, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_judgments(self) -> RagEvalBundle:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("eval bundle case identifiers must be unique")
        identities = {
            (
                " ".join(case.query.casefold().split()),
                case.tenant_id.casefold(),
                case.locale.casefold(),
            )
            for case in self.cases
        }
        if len(identities) != len(self.cases):
            raise ValueError("eval bundle contains duplicate query judgments")
        if len({case.category for case in self.cases}) > 100:
            raise ValueError("eval bundle is limited to 100 categories")
        if any(
            case.category == "unspecified" or not case.judgment_reference for case in self.cases
        ):
            raise ValueError("governed eval cases require category and judgment reference")
        if not any(case.must_block_as_dynamic for case in self.cases) or not any(
            not case.must_block_as_dynamic for case in self.cases
        ):
            raise ValueError("eval bundle must cover retrieval and dynamic-fact blocking")
        return self


@dataclass(frozen=True, slots=True)
class RagEvalAsset:
    cases: tuple[RagEvalCase, ...]
    bundle: RagEvalBundle | None = None

    def release_eligible(
        self,
        *,
        minimum_case_count: int,
        expected_catalog_sha256: str,
    ) -> bool:
        if self.bundle is None or self.bundle.approval_status != "approved":
            return False
        return (
            len(self.cases) >= minimum_case_count
            and bool(re.fullmatch(r"[0-9a-f]{64}", expected_catalog_sha256))
            and self.bundle.catalog_sha256 == expected_catalog_sha256
            and self.bundle.approved_at <= datetime.now(timezone.utc)
            and any(case.risk_level == "safety_critical" for case in self.cases)
        )

    def require_release_eligible(
        self,
        *,
        minimum_case_count: int,
        expected_catalog_sha256: str,
    ) -> None:
        if not 100 <= minimum_case_count <= 10_000:
            raise ValueError("production Eval minimum must be between 100 and 10000 cases")
        if self.bundle is None:
            raise ValueError("production Eval requires a versioned approved bundle")
        if self.bundle.approval_status != "approved":
            raise ValueError("production Eval bundle is not approved")
        if self.bundle.approved_at > datetime.now(timezone.utc):
            raise ValueError("production Eval approval timestamp is from the future")
        if len(self.cases) < minimum_case_count:
            raise ValueError(
                f"production Eval requires at least {minimum_case_count} distinct cases"
            )
        if re.fullmatch(r"[0-9a-f]{64}", expected_catalog_sha256) is None:
            raise ValueError("production Eval requires the staged catalog SHA-256")
        if self.bundle.catalog_sha256 != expected_catalog_sha256:
            raise ValueError("Eval bundle does not match the staged knowledge catalog")
        if not any(case.risk_level == "safety_critical" for case in self.cases):
            raise ValueError("production Eval must include safety-critical judgments")

    def governance_payload(
        self,
        *,
        required: bool,
        minimum_case_count: int,
        expected_catalog_sha256: str,
    ) -> dict[str, object]:
        category_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        judged = 0
        for case in self.cases:
            category_counts[case.category] = category_counts.get(case.category, 0) + 1
            risk_counts[case.risk_level] = risk_counts.get(case.risk_level, 0) + 1
            judged += bool(case.judgment_reference)
        bundle = self.bundle
        eligible = self.release_eligible(
            minimum_case_count=minimum_case_count,
            expected_catalog_sha256=expected_catalog_sha256,
        )
        return {
            "required": required,
            "schemaVersion": bundle.schema_version if bundle is not None else "legacy-array",
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
            "catalogSha256": bundle.catalog_sha256 if bundle is not None else None,
            "catalogMatchVerified": (
                bundle is not None
                and bool(re.fullmatch(r"[0-9a-f]{64}", expected_catalog_sha256))
                and bundle.catalog_sha256 == expected_catalog_sha256
            ),
            "caseCount": len(self.cases),
            "minimumCaseCount": minimum_case_count,
            "categoryCounts": dict(sorted(category_counts.items())),
            "riskLevelCounts": dict(sorted(risk_counts.items())),
            "judgmentCoverage": round(judged / len(self.cases), 6),
            "releaseEligible": eligible,
        }


@dataclass(frozen=True, slots=True)
class RagEvalFailure:
    case_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RagEvalObservation:
    case_id: str
    category: str
    risk_level: str
    passed: bool
    retrieval_case: bool
    retrieved_expected: int
    expected_documents: int
    reciprocal_rank: float
    relevant_citations: int
    observed_citations: int
    citation_integrity_passed: bool
    dynamic_case: bool
    dynamic_blocked: bool
    latency_ms: float


@dataclass(frozen=True, slots=True)
class RagEvalReport:
    total: int
    passed: int
    retrieval_cases: int
    retrieved_expected: int
    expected_documents: int
    dynamic_cases: int
    dynamic_blocked: int
    reciprocal_rank_sum: float
    relevant_citations: int
    observed_citations: int
    citation_integrity_passed: int
    latencies_ms: tuple[float, ...]
    failures: tuple[RagEvalFailure, ...]
    observations: tuple[RagEvalObservation, ...]

    @property
    def recall(self) -> float:
        return self.retrieved_expected / self.expected_documents if self.expected_documents else 1.0

    @property
    def dynamic_block_rate(self) -> float:
        return self.dynamic_blocked / self.dynamic_cases if self.dynamic_cases else 1.0

    @property
    def mean_reciprocal_rank(self) -> float:
        return self.reciprocal_rank_sum / self.retrieval_cases if self.retrieval_cases else 1.0

    @property
    def citation_precision(self) -> float:
        return self.relevant_citations / self.observed_citations if self.observed_citations else 1.0

    @property
    def citation_integrity_rate(self) -> float:
        return (
            self.citation_integrity_passed / self.retrieval_cases if self.retrieval_cases else 1.0
        )

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]

    @property
    def passed_red_lines(self) -> bool:
        return (
            not self.failures
            and self.dynamic_block_rate == 1.0
            and self.citation_integrity_rate == 1.0
        )

    def meets_thresholds(
        self,
        *,
        min_recall: float = 0.0,
        min_mrr: float = 0.0,
        min_precision: float = 0.0,
        max_p95_latency_ms: float | None = None,
    ) -> bool:
        return (
            self.passed_red_lines
            and self.recall >= min_recall
            and self.mean_reciprocal_rank >= min_mrr
            and self.citation_precision >= min_precision
            and (max_p95_latency_ms is None or self.p95_latency_ms <= max_p95_latency_ms)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "passed": self.passed,
            "retrievalCases": self.retrieval_cases,
            "recall": round(self.recall, 6),
            "meanReciprocalRank": round(self.mean_reciprocal_rank, 6),
            "citationPrecision": round(self.citation_precision, 6),
            "citationIntegrityRate": round(self.citation_integrity_rate, 6),
            "meanLatencyMs": round(self.mean_latency_ms, 3),
            "p95LatencyMs": round(self.p95_latency_ms, 3),
            "dynamicCases": self.dynamic_cases,
            "dynamicBlockRate": round(self.dynamic_block_rate, 6),
            "passedRedLines": self.passed_red_lines,
            "failures": [
                {"caseId": failure.case_id, "reason": failure.reason} for failure in self.failures
            ],
            "slices": {
                "category": self._slice_metrics("category"),
                "riskLevel": self._slice_metrics("risk_level"),
            },
        }

    def _slice_metrics(self, dimension: Literal["category", "risk_level"]) -> dict[str, object]:
        grouped: dict[str, list[RagEvalObservation]] = {}
        for observation in self.observations:
            key = getattr(observation, dimension)
            grouped.setdefault(key, []).append(observation)
        return {
            key: _observation_metrics(tuple(observations))
            for key, observations in sorted(grouped.items())
        }


def _observation_metrics(observations: Sequence[RagEvalObservation]) -> dict[str, object]:
    retrieval = tuple(item for item in observations if item.retrieval_case)
    dynamic = tuple(item for item in observations if item.dynamic_case)
    expected_documents = sum(item.expected_documents for item in retrieval)
    observed_citations = sum(item.observed_citations for item in retrieval)
    latencies = sorted(item.latency_ms for item in observations)
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0.0
    return {
        "total": len(observations),
        "passed": sum(item.passed for item in observations),
        "retrievalCases": len(retrieval),
        "recall": round(
            sum(item.retrieved_expected for item in retrieval) / expected_documents
            if expected_documents
            else 1.0,
            6,
        ),
        "meanReciprocalRank": round(
            sum(item.reciprocal_rank for item in retrieval) / len(retrieval) if retrieval else 1.0,
            6,
        ),
        "citationPrecision": round(
            sum(item.relevant_citations for item in retrieval) / observed_citations
            if observed_citations
            else 1.0,
            6,
        ),
        "citationIntegrityRate": round(
            sum(item.citation_integrity_passed for item in retrieval) / len(retrieval)
            if retrieval
            else 1.0,
            6,
        ),
        "dynamicCases": len(dynamic),
        "dynamicBlockRate": round(
            sum(item.dynamic_blocked for item in dynamic) / len(dynamic) if dynamic else 1.0,
            6,
        ),
        "meanLatencyMs": round(
            sum(item.latency_ms for item in observations) / len(observations)
            if observations
            else 0.0,
            3,
        ),
        "p95LatencyMs": round(p95, 3),
    }


@dataclass(frozen=True, slots=True)
class RagLoadReport:
    request_count: int
    concurrency: int
    duration_seconds: float
    latencies_ms: tuple[float, ...]

    @property
    def throughput_qps(self) -> float:
        return self.request_count / self.duration_seconds if self.duration_seconds > 0 else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]

    def meets_thresholds(
        self,
        *,
        min_requests: int,
        min_throughput_qps: float,
        max_p95_latency_ms: float,
    ) -> bool:
        return (
            self.request_count >= min_requests
            and self.throughput_qps >= min_throughput_qps
            and self.p95_latency_ms <= max_p95_latency_ms
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "requestCount": self.request_count,
            "concurrency": self.concurrency,
            "durationSeconds": round(self.duration_seconds, 6),
            "throughputQps": round(self.throughput_qps, 6),
            "meanLatencyMs": round(self.mean_latency_ms, 3),
            "p95LatencyMs": round(self.p95_latency_ms, 3),
        }


async def benchmark_rag(
    rag: StableKnowledgeRag,
    cases: Sequence[RagEvalCase],
    *,
    repetitions: int = 1,
    concurrency: int = 1,
    warmup_requests: int = 0,
    moment: datetime | None = None,
) -> RagLoadReport:
    """Run a bounded semantic retrieval load test without inflating dynamic-guard QPS."""

    retrieval_cases = tuple(case for case in cases if not case.must_block_as_dynamic)
    if not retrieval_cases:
        raise ValueError("RAG benchmark requires at least one retrieval case")
    if not 1 <= repetitions <= 100:
        raise ValueError("RAG benchmark repetitions must be between 1 and 100")
    if not 1 <= concurrency <= 64:
        raise ValueError("RAG benchmark concurrency must be between 1 and 64")
    if not 0 <= warmup_requests <= 1_000:
        raise ValueError("RAG benchmark warmup requests must be between 0 and 1000")
    request_count = len(retrieval_cases) * repetitions
    if request_count > 50_000:
        raise ValueError("RAG benchmark is limited to 50000 measured requests")
    observed_at = moment or datetime.now(timezone.utc)

    async def warmup(position: int) -> None:
        case = retrieval_cases[position % len(retrieval_cases)]
        await rag.prepare(
            case.query,
            tenant_id=case.tenant_id,
            locale=case.locale,
            moment=observed_at,
            experiment_key=f"warmup-{position}-{case.case_id}",
        )

    warmup_position = 0
    warmup_width = 1
    while warmup_position < warmup_requests:
        batch_size = min(warmup_width, warmup_requests - warmup_position)
        await asyncio.gather(
            *(warmup(position) for position in range(warmup_position, warmup_position + batch_size))
        )
        warmup_position += batch_size
        warmup_width = min(concurrency, warmup_width * 2)

    queue: asyncio.Queue[tuple[int, RagEvalCase]] = asyncio.Queue()
    for repetition in range(repetitions):
        for case in retrieval_cases:
            queue.put_nowait((repetition, case))
    latencies_ms: list[float] = []

    async def worker() -> None:
        while True:
            try:
                repetition, case = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                started_at = time.perf_counter()
                await rag.prepare(
                    case.query,
                    tenant_id=case.tenant_id,
                    locale=case.locale,
                    moment=observed_at,
                    experiment_key=f"load-{repetition}-{case.case_id}",
                )
                latencies_ms.append((time.perf_counter() - started_at) * 1000)
            finally:
                queue.task_done()

    started_at = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(min(concurrency, request_count))))
    duration_seconds = time.perf_counter() - started_at
    if len(latencies_ms) != request_count:
        raise RuntimeError("RAG benchmark did not complete every request")
    return RagLoadReport(
        request_count=request_count,
        concurrency=concurrency,
        duration_seconds=duration_seconds,
        latencies_ms=tuple(latencies_ms),
    )


async def evaluate_rag(
    rag: StableKnowledgeRag,
    cases: Sequence[RagEvalCase],
    *,
    moment: datetime | None = None,
) -> RagEvalReport:
    if not cases:
        raise ValueError("RAG evaluation set must not be empty")
    observed_at = moment or datetime.now(timezone.utc)
    failures: list[RagEvalFailure] = []
    retrieval_cases = 0
    retrieved_expected = 0
    expected_documents = 0
    dynamic_cases = 0
    dynamic_blocked = 0
    reciprocal_rank_sum = 0.0
    relevant_citations = 0
    observed_citations = 0
    citation_integrity_passed = 0
    latencies_ms: list[float] = []
    observations: list[RagEvalObservation] = []
    passed = 0
    for case in cases:
        started_at = time.perf_counter()
        bundle = await rag.prepare(
            case.query,
            tenant_id=case.tenant_id,
            locale=case.locale,
            moment=observed_at,
            experiment_key=case.case_id,
        )
        latency_ms = (time.perf_counter() - started_at) * 1000
        latencies_ms.append(latency_ms)
        if case.must_block_as_dynamic:
            dynamic_cases += 1
            blocked = bundle.outcome == "dynamic_blocked" and not bundle.citations
            if blocked:
                dynamic_blocked += 1
                passed += 1
            else:
                failures.append(RagEvalFailure(case.case_id, "dynamic query was not blocked"))
            observations.append(
                RagEvalObservation(
                    case_id=case.case_id,
                    category=case.category,
                    risk_level=case.risk_level,
                    passed=blocked,
                    retrieval_case=False,
                    retrieved_expected=0,
                    expected_documents=0,
                    reciprocal_rank=0.0,
                    relevant_citations=0,
                    observed_citations=0,
                    citation_integrity_passed=True,
                    dynamic_case=True,
                    dynamic_blocked=blocked,
                    latency_ms=latency_ms,
                )
            )
            continue
        retrieval_cases += 1
        expected = set(case.expected_document_ids)
        observed_order = [citation.document_id for citation in bundle.citations]
        observed = set(observed_order)
        matches = len(expected & observed)
        retrieved_expected += matches
        expected_documents += len(expected)
        relevant_citations += matches
        observed_citations += len(observed_order)
        reciprocal_rank = 0.0
        for rank, document_id in enumerate(observed_order, start=1):
            if document_id in expected:
                reciprocal_rank = 1 / rank
                reciprocal_rank_sum += reciprocal_rank
                break
        expected_citation_ids = [f"K{index}" for index in range(1, len(bundle.citations) + 1)]
        citation_ids = [citation.citation_id for citation in bundle.citations]
        integrity_ok = (
            citation_ids == expected_citation_ids
            and len(observed_order) == len(observed)
            and all(citation.source.startswith("https://") for citation in bundle.citations)
        )
        if integrity_ok:
            citation_integrity_passed += 1

        reasons: list[str] = []
        if not expected <= observed:
            reasons.append(f"missing documents: {','.join(sorted(expected - observed))}")
        forbidden = set(case.forbidden_document_ids) & observed
        if forbidden:
            reasons.append(f"forbidden documents: {','.join(sorted(forbidden))}")
        if case.expected_top_document_id is not None and (
            not observed_order or observed_order[0] != case.expected_top_document_id
        ):
            reasons.append(f"top document is not {case.expected_top_document_id}")
        if not integrity_ok:
            reasons.append("citation integrity check failed")
        if not reasons:
            passed += 1
        else:
            failures.append(RagEvalFailure(case.case_id, "; ".join(reasons)))
        observations.append(
            RagEvalObservation(
                case_id=case.case_id,
                category=case.category,
                risk_level=case.risk_level,
                passed=not reasons,
                retrieval_case=True,
                retrieved_expected=matches,
                expected_documents=len(expected),
                reciprocal_rank=reciprocal_rank,
                relevant_citations=matches,
                observed_citations=len(observed_order),
                citation_integrity_passed=integrity_ok,
                dynamic_case=False,
                dynamic_blocked=False,
                latency_ms=latency_ms,
            )
        )
    return RagEvalReport(
        total=len(cases),
        passed=passed,
        retrieval_cases=retrieval_cases,
        retrieved_expected=retrieved_expected,
        expected_documents=expected_documents,
        dynamic_cases=dynamic_cases,
        dynamic_blocked=dynamic_blocked,
        reciprocal_rank_sum=reciprocal_rank_sum,
        relevant_citations=relevant_citations,
        observed_citations=observed_citations,
        citation_integrity_passed=citation_integrity_passed,
        latencies_ms=tuple(latencies_ms),
        failures=tuple(failures),
        observations=tuple(observations),
    )


def load_eval_asset(path: str | Path) -> RagEvalAsset:
    eval_path = Path(path).resolve()
    if not eval_path.is_file() or eval_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("RAG evaluation set must be a bounded JSON file")
    try:
        raw = json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("RAG evaluation set is not valid UTF-8 JSON") from exc
    if isinstance(raw, list):
        if not 1 <= len(raw) <= 10_000:
            raise ValueError("RAG evaluation set must be bounded and nonempty")
        cases = tuple(RagEvalCase.model_validate(item) for item in raw)
        if len({case.category for case in cases}) > 100:
            raise ValueError("RAG evaluation set is limited to 100 categories")
        return RagEvalAsset(cases=cases)
    if not isinstance(raw, dict):
        raise ValueError("RAG evaluation set must be a legacy array or versioned bundle")
    bundle = RagEvalBundle.model_validate(raw)
    return RagEvalAsset(cases=bundle.cases, bundle=bundle)


def load_eval_cases(path: str | Path) -> tuple[RagEvalCase, ...]:
    """Compatibility loader for local fixture callers."""

    return load_eval_asset(path).cases


def validate_release_eval_governance(
    payload: object,
    *,
    minimum_case_count: int = 100,
    now: datetime | None = None,
) -> None:
    """Validate the compact governance evidence embedded in an acceptance report."""

    if not isinstance(payload, dict):
        raise ValueError("acceptance report lacks Eval governance evidence")

    def integer(key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Eval governance {key} is invalid")
        return value

    case_count = integer("caseCount")
    declared_minimum = integer("minimumCaseCount")
    reviewer_count = integer("reviewerCount")
    dataset_id = payload.get("datasetId")
    dataset_version = payload.get("datasetVersion")
    owner_team = payload.get("ownerTeam")
    approval_reference = payload.get("approvalReference")
    catalog_sha256 = payload.get("catalogSha256")
    if (
        payload.get("required") is not True
        or payload.get("schemaVersion") != "damai.rag.eval/v1"
        or payload.get("approved") is not True
        or payload.get("catalogMatchVerified") is not True
        or payload.get("releaseEligible") is not True
        or case_count < max(minimum_case_count, declared_minimum)
        or not 100 <= declared_minimum <= 10_000
        or not 2 <= reviewer_count <= 20
        or not all(
            isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
            for value in (dataset_id, dataset_version, owner_team)
        )
        or not isinstance(approval_reference, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}", approval_reference) is None
        or not isinstance(catalog_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", catalog_sha256) is None
        or payload.get("judgmentCoverage") != 1.0
    ):
        raise ValueError("acceptance report Eval governance evidence is invalid")

    category_counts = payload.get("categoryCounts")
    risk_counts = payload.get("riskLevelCounts")
    if not isinstance(category_counts, dict) or not 1 <= len(category_counts) <= 100:
        raise ValueError("acceptance report Eval category evidence is invalid")
    if not isinstance(risk_counts, dict):
        raise ValueError("acceptance report Eval risk evidence is invalid")

    def valid_counts(counts: object) -> bool:
        return isinstance(counts, dict) and all(
            isinstance(key, str)
            and bool(key)
            and not isinstance(value, bool)
            and isinstance(value, int)
            and value > 0
            for key, value in counts.items()
        )

    if (
        not valid_counts(category_counts)
        or not valid_counts(risk_counts)
        or "unspecified" in category_counts
        or set(risk_counts) - {"standard", "safety_critical"}
        or risk_counts.get("safety_critical", 0) < 1
        or sum(category_counts.values()) != case_count
        or sum(risk_counts.values()) != case_count
    ):
        raise ValueError("acceptance report Eval slice evidence is invalid")

    approved_at = payload.get("approvedAt")
    if not isinstance(approved_at, str):
        raise ValueError("acceptance report Eval approval timestamp is invalid")
    try:
        approval_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("acceptance report Eval approval timestamp is invalid") from exc
    current = now or datetime.now(timezone.utc)
    if approval_time.tzinfo is None or approval_time > current:
        raise ValueError("acceptance report Eval approval timestamp is invalid")
