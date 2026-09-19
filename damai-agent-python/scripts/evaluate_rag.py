from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from damai_agent.elasticsearch_rag import ElasticsearchKnowledgeRetriever
from damai_agent.rag import (
    KnowledgeRetriever,
    RetrievalProfile,
    StableKnowledgeRag,
    load_knowledge_catalog,
)
from damai_agent.rag_eval import benchmark_rag, evaluate_rag, load_eval_cases

_COST_EVIDENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate stable-knowledge retrieval red lines")
    parser.add_argument("--backend", choices=("local", "elasticsearch"), default="local")
    parser.add_argument("--catalog")
    parser.add_argument("--eval-set", required=True)
    parser.add_argument("--source-host", action="append", required=True)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--candidate-k", type=int, default=12)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--rrf-rank-constant", type=int, default=60)
    parser.add_argument(
        "--rank-window-size",
        type=int,
        default=int(os.environ.get("DAMAI_EVAL_ELASTICSEARCH_RANK_WINDOW_SIZE", "50")),
    )
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--min-mrr", type=float, default=0.8)
    parser.add_argument("--min-precision", type=float, default=0.5)
    parser.add_argument("--max-p95-latency-ms", type=float, default=500.0)
    parser.add_argument(
        "--retrieval-profile",
        choices=("lexical", "semantic_hybrid", "semantic_rerank"),
        default="lexical",
    )
    parser.add_argument(
        "--elasticsearch-url",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_URL", ""),
    )
    parser.add_argument(
        "--index-alias",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_INDEX_ALIAS", ""),
    )
    parser.add_argument(
        "--index-name",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_INDEX", ""),
        help="Exact staged index. Required for semantic release acceptance.",
    )
    parser.add_argument(
        "--index-version",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_INDEX_VERSION", ""),
    )
    parser.add_argument(
        "--semantic-field",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_SEMANTIC_FIELD", "semantic_content"),
    )
    parser.add_argument(
        "--rerank-inference-id",
        default=os.environ.get("DAMAI_EVAL_ELASTICSEARCH_RERANK_INFERENCE_ID", ""),
    )
    parser.add_argument("--benchmark-repetitions", type=int, default=1)
    parser.add_argument("--benchmark-concurrency", type=int, default=1)
    parser.add_argument("--warmup-requests", type=int, default=0)
    parser.add_argument("--min-benchmark-requests", type=int, default=30)
    parser.add_argument("--min-throughput-qps", type=float, default=0.0)
    parser.add_argument(
        "--observed-indexing-cost-usd",
        default=os.environ.get("DAMAI_EVAL_OBSERVED_INDEXING_COST_USD", ""),
    )
    parser.add_argument(
        "--observed-query-cost-usd",
        default=os.environ.get("DAMAI_EVAL_OBSERVED_QUERY_COST_USD", ""),
    )
    parser.add_argument(
        "--cost-evidence",
        default=os.environ.get("DAMAI_EVAL_COST_EVIDENCE", ""),
        help="Non-secret billing or provider usage evidence identifier.",
    )
    parser.add_argument("--max-total-cost-usd")
    parser.add_argument("--max-query-cost-per-1k-usd")
    parser.add_argument("--report-out", type=Path)
    return parser.parse_args()


def usd_to_micro(value: str, *, label: str) -> int | None:
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a decimal USD amount") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        raise ValueError(f"{label} is outside the accepted range")
    return int((amount * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def bounded_cost_evidence(value: str) -> str:
    if value and _COST_EVIDENCE_PATTERN.fullmatch(value) is None:
        raise ValueError("cost evidence identifier is invalid")
    return value


def write_report(path: Path, payload: dict[str, object]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest()


async def evaluate() -> int:
    args = parse_args()
    if not 10 <= args.rank_window_size <= 200:
        raise ValueError("Elasticsearch rank window must be between 10 and 200")
    if args.rank_window_size < max(args.top_k, args.candidate_k):
        raise ValueError("Elasticsearch rank window must cover top-k and candidate-k")
    index: KnowledgeRetriever
    target_index = "local"
    semantic_configuration: dict[str, object] = {}
    if args.backend == "local":
        if not args.catalog:
            raise ValueError("--catalog is required for the local backend")
        index = load_knowledge_catalog(args.catalog, allowed_source_hosts=tuple(args.source_host))
    else:
        api_key = os.environ.get("DAMAI_EVAL_ELASTICSEARCH_API_KEY", "")
        target_index = args.index_name or args.index_alias
        if not all((args.elasticsearch_url, api_key, target_index, args.index_version)):
            raise ValueError("Elasticsearch Eval environment is incomplete")
        if args.retrieval_profile != "lexical" and not args.index_name:
            raise ValueError("semantic acceptance must target an exact staged --index-name")
        profile = cast(
            RetrievalProfile,
            args.retrieval_profile.replace("_", "-")
            if args.retrieval_profile != "lexical"
            else "elastic-lexical",
        )
        index = ElasticsearchKnowledgeRetriever(
            args.elasticsearch_url,
            api_key,
            target_index,
            args.index_version,
            tuple(args.source_host),
            retrieval_profile=profile,
            semantic_field=args.semantic_field,
            rerank_inference_id=args.rerank_inference_id,
            rank_window_size=args.rank_window_size,
            rank_constant=args.rrf_rank_constant,
        )
        if not await index.check_ready():
            raise RuntimeError("Elasticsearch semantic target is not ready")
        if args.retrieval_profile != "lexical":
            semantic_configuration = await index.semantic_configuration()
    if args.hybrid:
        from damai_agent.rag import ReciprocalRankFusionRetriever

        index = ReciprocalRankFusionRetriever(index, rank_constant=args.rrf_rank_constant)
    rag = StableKnowledgeRag(
        index,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        rerank_rollout_percent=100 if args.rerank else 0,
    )
    cases = load_eval_cases(args.eval_set)
    quality = await evaluate_rag(rag, cases)
    quality_passed = quality.meets_thresholds(
        min_recall=args.min_recall,
        min_mrr=args.min_mrr,
        min_precision=args.min_precision,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )
    load = await benchmark_rag(
        rag,
        cases,
        repetitions=args.benchmark_repetitions,
        concurrency=args.benchmark_concurrency,
        warmup_requests=args.warmup_requests,
    )
    min_requests = args.min_benchmark_requests if args.backend == "elasticsearch" else 1
    load_passed = load.meets_thresholds(
        min_requests=min_requests,
        min_throughput_qps=args.min_throughput_qps,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )

    indexing_cost = usd_to_micro(args.observed_indexing_cost_usd, label="observed indexing cost")
    query_cost = usd_to_micro(args.observed_query_cost_usd, label="observed query cost")
    max_total_cost = usd_to_micro(args.max_total_cost_usd or "", label="maximum total cost")
    max_query_cost = usd_to_micro(
        args.max_query_cost_per_1k_usd or "", label="maximum query cost per 1000 requests"
    )
    evidence = bounded_cost_evidence(args.cost_evidence)
    requires_cost = args.backend == "elasticsearch" and args.retrieval_profile != "lexical"
    query_cost_per_1k = (
        (query_cost * 1000 + load.request_count - 1) // load.request_count
        if query_cost is not None
        else None
    )
    total_cost = (
        indexing_cost + query_cost if indexing_cost is not None and query_cost is not None else None
    )
    cost_passed = not requires_cost or (
        indexing_cost is not None
        and query_cost is not None
        and bool(evidence)
        and max_total_cost is not None
        and max_query_cost is not None
        and total_cost is not None
        and query_cost_per_1k is not None
        and total_cost <= max_total_cost
        and query_cost_per_1k <= max_query_cost
    )

    quality_payload = quality.to_dict()
    quality_payload.update(
        {
            "minRecall": args.min_recall,
            "minMeanReciprocalRank": args.min_mrr,
            "minCitationPrecision": args.min_precision,
            "maxP95LatencyMs": args.max_p95_latency_ms,
            "passed": quality_passed,
        }
    )
    load_payload = load.to_dict()
    load_payload.update(
        {
            "minRequests": min_requests,
            "minThroughputQps": args.min_throughput_qps,
            "maxP95LatencyMs": args.max_p95_latency_ms,
            "passed": load_passed,
        }
    )
    cost_payload: dict[str, object] = {
        "required": requires_cost,
        "evidence": evidence,
        "observedIndexingCostMicroUsd": indexing_cost,
        "observedQueryCostMicroUsd": query_cost,
        "observedTotalCostMicroUsd": total_cost,
        "queryCostPer1kMicroUsd": query_cost_per_1k,
        "maxTotalCostMicroUsd": max_total_cost,
        "maxQueryCostPer1kMicroUsd": max_query_cost,
        "passed": cost_passed,
    }
    eval_set_sha256 = await asyncio.to_thread(sha256_file, args.eval_set)
    acceptance: dict[str, object] = {
        "schemaVersion": "damai.rag.acceptance/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "targetIndex": target_index,
        "indexVersion": args.index_version
        if args.backend == "elasticsearch"
        else index.index_version,
        "retrievalProfile": args.retrieval_profile,
        "evalSetSha256": eval_set_sha256,
        "benchmarkConfiguration": {
            "backend": args.backend,
            "topK": args.top_k,
            "candidateK": args.candidate_k,
            "rankWindowSize": args.rank_window_size,
            "rrfRankConstant": args.rrf_rank_constant,
            "repetitions": args.benchmark_repetitions,
            "concurrency": args.benchmark_concurrency,
            "warmupRequests": args.warmup_requests,
        },
        "semanticConfiguration": semantic_configuration,
        "quality": quality_payload,
        "load": load_payload,
        "cost": cost_payload,
        "passed": quality_passed and load_passed and cost_passed,
    }
    print(json.dumps(acceptance, ensure_ascii=False, indent=2))
    if args.report_out is not None:
        await asyncio.to_thread(write_report, args.report_out, acceptance)
    return 0 if acceptance["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate()))
