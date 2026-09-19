from __future__ import annotations

import argparse
import asyncio
import json

from damai_agent.rag import StableKnowledgeRag, load_knowledge_catalog
from damai_agent.rag_eval import evaluate_rag, load_eval_cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate stable-knowledge retrieval red lines")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--eval-set", required=True)
    parser.add_argument("--source-host", action="append", required=True)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--candidate-k", type=int, default=12)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--rrf-rank-constant", type=int, default=60)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--min-mrr", type=float, default=0.8)
    parser.add_argument("--min-precision", type=float, default=0.5)
    parser.add_argument("--max-p95-latency-ms", type=float, default=500.0)
    return parser.parse_args()


async def evaluate() -> int:
    args = parse_args()
    index = load_knowledge_catalog(args.catalog, allowed_source_hosts=tuple(args.source_host))
    if args.hybrid:
        from damai_agent.rag import ReciprocalRankFusionRetriever

        index = ReciprocalRankFusionRetriever(  # type: ignore[assignment]
            index, rank_constant=args.rrf_rank_constant
        )
    report = await evaluate_rag(
        StableKnowledgeRag(
            index,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            rerank_rollout_percent=100 if args.rerank else 0,
        ),
        load_eval_cases(args.eval_set),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return (
        0
        if report.meets_thresholds(
            min_recall=args.min_recall,
            min_mrr=args.min_mrr,
            min_precision=args.min_precision,
            max_p95_latency_ms=args.max_p95_latency_ms,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate()))
