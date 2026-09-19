"""Attach observed cloud billing evidence to an immutable RAG benchmark report."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.evaluate_rag import bounded_cost_evidence, usd_to_micro, write_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-report", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument("--observed-indexing-cost-usd", required=True)
    parser.add_argument("--observed-query-cost-usd", required=True)
    parser.add_argument("--cost-evidence", required=True)
    parser.add_argument("--cost-evidence-file", type=Path, required=True)
    parser.add_argument("--max-total-cost-usd", required=True)
    parser.add_argument("--max-query-cost-per-1k-usd", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.benchmark_report.resolve()
    target = args.report_out.resolve()
    if source == target:
        raise ValueError("cost attestation must create a new report")
    if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("benchmark report is missing or too large")
    raw = source.read_bytes()
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("benchmark report must be valid UTF-8 JSON") from exc
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != "damai.rag.acceptance/v1"
        or report.get("retrievalProfile") not in {"semantic_hybrid", "semantic_rerank"}
    ):
        raise ValueError("benchmark report schema or profile is invalid")
    quality = report.get("quality")
    load = report.get("load")
    if (
        not isinstance(quality, dict)
        or quality.get("passed") is not True
        or not isinstance(load, dict)
        or load.get("passed") is not True
    ):
        raise ValueError("quality and load gates must pass before cost attestation")
    request_count = load.get("requestCount")
    if isinstance(request_count, bool) or not isinstance(request_count, int) or request_count <= 0:
        raise ValueError("benchmark request count is invalid")

    indexing_cost = usd_to_micro(args.observed_indexing_cost_usd, label="observed indexing cost")
    query_cost = usd_to_micro(args.observed_query_cost_usd, label="observed query cost")
    max_total_cost = usd_to_micro(args.max_total_cost_usd, label="maximum total cost")
    max_query_cost = usd_to_micro(
        args.max_query_cost_per_1k_usd, label="maximum query cost per 1000 requests"
    )
    if None in {indexing_cost, query_cost, max_total_cost, max_query_cost}:
        raise ValueError("all cost values are required")
    assert indexing_cost is not None
    assert query_cost is not None
    assert max_total_cost is not None
    assert max_query_cost is not None
    total_cost = indexing_cost + query_cost
    query_cost_per_1k = (query_cost * 1000 + request_count - 1) // request_count
    cost_passed = total_cost <= max_total_cost and query_cost_per_1k <= max_query_cost
    evidence = bounded_cost_evidence(args.cost_evidence)
    if not evidence:
        raise ValueError("cost evidence identifier is required")
    evidence_file = args.cost_evidence_file.resolve()
    if not evidence_file.is_file() or not 0 < evidence_file.stat().st_size <= 20 * 1024 * 1024:
        raise ValueError("cost evidence file is missing, empty, or too large")
    evidence_sha256 = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
    report["benchmarkReportSha256"] = hashlib.sha256(raw).hexdigest()
    report["costAttestedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["cost"] = {
        "required": True,
        "evidence": evidence,
        "evidenceSha256": evidence_sha256,
        "observedIndexingCostMicroUsd": indexing_cost,
        "observedQueryCostMicroUsd": query_cost,
        "observedTotalCostMicroUsd": total_cost,
        "queryCostPer1kMicroUsd": query_cost_per_1k,
        "maxTotalCostMicroUsd": max_total_cost,
        "maxQueryCostPer1kMicroUsd": max_query_cost,
        "passed": cost_passed,
    }
    report["passed"] = cost_passed
    write_report(target, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if cost_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
