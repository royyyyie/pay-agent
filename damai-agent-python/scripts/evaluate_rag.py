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
    return parser.parse_args()


async def evaluate() -> int:
    args = parse_args()
    index = load_knowledge_catalog(args.catalog, allowed_source_hosts=tuple(args.source_host))
    report = await evaluate_rag(
        StableKnowledgeRag(index, top_k=args.top_k), load_eval_cases(args.eval_set)
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.passed_red_lines else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate()))
