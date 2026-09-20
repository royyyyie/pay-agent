"""Compare governed baseline and candidate RAG reports with paired statistics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from damai_agent.evidence import write_report
from damai_agent.rag_experiment import RagHumanReviewEvidence, compare_rag_experiment

_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--review-evidence", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("non_inferiority", "superiority"), default="non_inferiority"
    )
    parser.add_argument("--margin", type=float, default=0.02)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-samples", type=int, default=5_000)
    parser.add_argument("--report-out", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path, *, label: str) -> tuple[dict[str, object], str]:
    resolved = path.resolve()
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= _MAX_EVIDENCE_BYTES:
        raise ValueError(f"{label} must be a bounded nonempty JSON file")
    raw = resolved.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def main() -> int:
    args = parse_args()
    baseline, baseline_hash = load_json(args.baseline_report, label="baseline report")
    candidate, candidate_hash = load_json(args.candidate_report, label="candidate report")
    review_payload, review_hash = load_json(args.review_evidence, label="review evidence")
    review = RagHumanReviewEvidence.model_validate(review_payload)
    report = compare_rag_experiment(
        baseline,
        candidate,
        review,
        baseline_sha256=baseline_hash,
        candidate_sha256=candidate_hash,
        review_sha256=review_hash,
        mode=args.mode,
        margin=args.margin,
        confidence=args.confidence,
        bootstrap_samples=args.bootstrap_samples,
    )
    write_report(args.report_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
