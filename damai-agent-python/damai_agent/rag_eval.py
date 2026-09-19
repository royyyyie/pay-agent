"""Offline retrieval and dynamic-fact red-line evaluation."""

from __future__ import annotations

import json
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
    must_block_as_dynamic: bool = False

    @model_validator(mode="after")
    def validate_expectation(self) -> RagEvalCase:
        if self.must_block_as_dynamic == bool(self.expected_document_ids):
            raise ValueError("eval case must define exactly one expected outcome")
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
    failures: tuple[RagEvalFailure, ...]

    @property
    def recall(self) -> float:
        return self.retrieved_expected / self.expected_documents if self.expected_documents else 1.0

    @property
    def dynamic_block_rate(self) -> float:
        return self.dynamic_blocked / self.dynamic_cases if self.dynamic_cases else 1.0

    @property
    def passed_red_lines(self) -> bool:
        return not self.failures and self.dynamic_block_rate == 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "passed": self.passed,
            "retrievalCases": self.retrieval_cases,
            "recall": round(self.recall, 6),
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
    passed = 0
    for case in cases:
        bundle = await rag.prepare(
            case.query,
            tenant_id=case.tenant_id,
            locale=case.locale,
            moment=observed_at,
        )
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
        observed = {citation.document_id for citation in bundle.citations}
        matches = len(expected & observed)
        retrieved_expected += matches
        expected_documents += len(expected)
        if expected <= observed:
            passed += 1
        else:
            missing = ",".join(sorted(expected - observed))
            failures.append(RagEvalFailure(case.case_id, f"missing documents: {missing}"))
    return RagEvalReport(
        total=len(cases),
        passed=passed,
        retrieval_cases=retrieval_cases,
        retrieved_expected=retrieved_expected,
        expected_documents=expected_documents,
        dynamic_cases=dynamic_cases,
        dynamic_blocked=dynamic_blocked,
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
