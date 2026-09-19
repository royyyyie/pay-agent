"""Offline retrieval and dynamic-fact red-line evaluation."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


@dataclass(frozen=True, slots=True)
class RagEvalFailure:
    case_id: str
    reason: str


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
        }


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
        latencies_ms.append((time.perf_counter() - started_at) * 1000)
        if case.must_block_as_dynamic:
            dynamic_cases += 1
            if bundle.outcome == "dynamic_blocked" and not bundle.citations:
                dynamic_blocked += 1
                passed += 1
            else:
                failures.append(RagEvalFailure(case.case_id, "dynamic query was not blocked"))
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
        for rank, document_id in enumerate(observed_order, start=1):
            if document_id in expected:
                reciprocal_rank_sum += 1 / rank
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
    )


def load_eval_cases(path: str | Path) -> tuple[RagEvalCase, ...]:
    eval_path = Path(path).resolve()
    try:
        raw = json.loads(eval_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("RAG evaluation set is not valid UTF-8 JSON") from exc
    if not isinstance(raw, list) or not 1 <= len(raw) <= 10_000:
        raise ValueError("RAG evaluation set must be a bounded nonempty JSON array")
    return tuple(RagEvalCase.model_validate(item) for item in raw)
