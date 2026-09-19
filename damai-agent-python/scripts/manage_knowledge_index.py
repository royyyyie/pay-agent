"""Validate, publish, roll back, or clean up an Elasticsearch knowledge release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from damai_agent.knowledge_publish import (
    ElasticsearchKnowledgePublisher,
    configure_semantic_mapping,
    configure_serverless_index_definition,
)
from damai_agent.rag import InMemoryKnowledgeIndex, load_knowledge_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = PROJECT_ROOT / "docs" / "elasticsearch-knowledge-index.json"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_COST_EVIDENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_INFERENCE_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("validate", "stage", "promote", "publish", "rollback", "delete")
    )
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--source-host", action="append", default=[])
    parser.add_argument("--url", default=os.environ.get("DAMAI_KNOWLEDGE_PUBLISH_URL", ""))
    parser.add_argument("--alias", default="damai-knowledge-read")
    parser.add_argument("--index")
    parser.add_argument("--confirm-index")
    parser.add_argument("--acceptance-report", type=Path)
    parser.add_argument("--max-report-age-hours", type=int, default=72)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument(
        "--serverless",
        action="store_true",
        help="Remove shard and replica settings managed by Elasticsearch Serverless.",
    )
    parser.add_argument(
        "--semantic-inference-id",
        default=os.environ.get("DAMAI_KNOWLEDGE_SEMANTIC_INFERENCE_ID", ""),
    )
    parser.add_argument(
        "--semantic-search-inference-id",
        default=os.environ.get("DAMAI_KNOWLEDGE_SEARCH_INFERENCE_ID", ""),
    )
    parser.add_argument("--chunking-strategy", choices=("sentence", "word"))
    parser.add_argument("--max-chunk-size", type=int)
    parser.add_argument("--chunk-overlap", type=int)
    return parser.parse_args()


def load_release(args: argparse.Namespace) -> InMemoryKnowledgeIndex:
    if args.catalog is None:
        raise ValueError("--catalog is required")
    if not args.source_host:
        raise ValueError("at least one --source-host is required")
    return load_knowledge_catalog(
        args.catalog,
        allowed_source_hosts=tuple(args.source_host),
    )


def require_confirmation(args: argparse.Namespace) -> str:
    if not args.index or args.confirm_index != args.index:
        raise ValueError("--confirm-index must exactly match --index")
    return str(args.index)


def build_publisher(args: argparse.Namespace) -> ElasticsearchKnowledgePublisher:
    api_key = os.environ.get("DAMAI_KNOWLEDGE_PUBLISH_API_KEY", "")
    if not args.url or not api_key:
        raise ValueError("publisher URL and API key are required")
    if urlparse(args.url).scheme != "https" and not args.allow_http:
        raise ValueError("publisher URL must use HTTPS unless --allow-http is explicit")
    return ElasticsearchKnowledgePublisher(
        args.url,
        api_key,
        args.alias,
        timeout_seconds=args.timeout_seconds,
        serverless=args.serverless,
    )


def verify_acceptance_report(args: argparse.Namespace, index_name: str) -> str:
    path = args.acceptance_report
    if path is None or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("a bounded --acceptance-report is required for promotion")
    raw = path.read_bytes()
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("acceptance report must be valid UTF-8 JSON") from exc
    if not isinstance(report, dict) or report.get("schemaVersion") != "damai.rag.acceptance/v1":
        raise ValueError("acceptance report schema is invalid")
    if report.get("targetIndex") != index_name or report.get("passed") is not True:
        raise ValueError("acceptance report does not approve the target index")
    if report.get("retrievalProfile") not in {"semantic_hybrid", "semantic_rerank"}:
        raise ValueError("acceptance report did not exercise a semantic profile")
    semantic = report.get("semanticConfiguration")
    inference_id = semantic.get("inferenceId") if isinstance(semantic, dict) else None
    chunking = semantic.get("chunkingSettings") if isinstance(semantic, dict) else None
    chunking_strategy = chunking.get("strategy") if isinstance(chunking, dict) else None
    chunk_size = chunking.get("max_chunk_size") if isinstance(chunking, dict) else None
    overlap_key = "sentence_overlap" if chunking_strategy == "sentence" else "overlap"
    chunk_overlap = chunking.get(overlap_key) if isinstance(chunking, dict) else None
    rerank_inference_id = semantic.get("rerankInferenceId") if isinstance(semantic, dict) else None
    if (
        not isinstance(inference_id, str)
        or _INFERENCE_PATTERN.fullmatch(inference_id) is None
        or not isinstance(chunking, dict)
        or chunking_strategy not in {"sentence", "word"}
        or isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or not 20 <= chunk_size <= 500
        or isinstance(chunk_overlap, bool)
        or not isinstance(chunk_overlap, int)
        or chunk_overlap < 0
        or (chunking_strategy == "sentence" and chunk_overlap not in {0, 1})
        or (chunking_strategy == "word" and chunk_overlap > chunk_size // 2)
        or (
            report.get("retrievalProfile") == "semantic_rerank"
            and (
                not isinstance(rerank_inference_id, str)
                or _INFERENCE_PATTERN.fullmatch(rerank_inference_id) is None
            )
        )
    ):
        raise ValueError("acceptance report lacks semantic mapping evidence")
    eval_hash = report.get("evalSetSha256")
    benchmark_hash = report.get("benchmarkReportSha256")
    if (
        not isinstance(eval_hash, str)
        or _SHA256_PATTERN.fullmatch(eval_hash) is None
        or not isinstance(benchmark_hash, str)
        or _SHA256_PATTERN.fullmatch(benchmark_hash) is None
    ):
        raise ValueError("acceptance report evidence hashes are invalid")

    quality = report.get("quality")
    load = report.get("load")
    cost = report.get("cost")
    if not all(isinstance(section, dict) for section in (quality, load, cost)):
        raise ValueError("acceptance report has incomplete quality, load, or cost evidence")
    assert isinstance(quality, dict)
    assert isinstance(load, dict)
    assert isinstance(cost, dict)

    def number(section: dict[str, object], key: str) -> float:
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"acceptance report metric {key} is invalid")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"acceptance report metric {key} is invalid")
        return result

    if (
        quality.get("passed") is not True
        or number(quality, "recall") < number(quality, "minRecall")
        or number(quality, "meanReciprocalRank") < number(quality, "minMeanReciprocalRank")
        or number(quality, "citationPrecision") < number(quality, "minCitationPrecision")
        or number(quality, "citationIntegrityRate") != 1.0
        or number(quality, "dynamicBlockRate") != 1.0
        or number(quality, "p95LatencyMs") > number(quality, "maxP95LatencyMs")
        or quality.get("failures") != []
    ):
        raise ValueError("acceptance report quality evidence failed verification")

    request_count = int(number(load, "requestCount"))
    min_requests = int(number(load, "minRequests"))
    if (
        load.get("passed") is not True
        or request_count < max(30, min_requests)
        or number(load, "throughputQps") < number(load, "minThroughputQps")
        or number(load, "p95LatencyMs") > number(load, "maxP95LatencyMs")
    ):
        raise ValueError("acceptance report load evidence failed verification")

    evidence = cost.get("evidence")
    evidence_hash = cost.get("evidenceSha256")
    indexing_cost = int(number(cost, "observedIndexingCostMicroUsd"))
    query_cost = int(number(cost, "observedQueryCostMicroUsd"))
    total_cost = int(number(cost, "observedTotalCostMicroUsd"))
    query_cost_per_1k = int(number(cost, "queryCostPer1kMicroUsd"))
    max_total_cost = int(number(cost, "maxTotalCostMicroUsd"))
    max_query_cost = int(number(cost, "maxQueryCostPer1kMicroUsd"))
    expected_query_cost_per_1k = (query_cost * 1000 + request_count - 1) // request_count
    if (
        cost.get("required") is not True
        or cost.get("passed") is not True
        or not isinstance(evidence, str)
        or _COST_EVIDENCE_PATTERN.fullmatch(evidence) is None
        or not isinstance(evidence_hash, str)
        or _SHA256_PATTERN.fullmatch(evidence_hash) is None
        or min(
            indexing_cost,
            query_cost,
            total_cost,
            query_cost_per_1k,
            max_total_cost,
            max_query_cost,
        )
        < 0
        or total_cost != indexing_cost + query_cost
        or query_cost_per_1k != expected_query_cost_per_1k
        or total_cost > max_total_cost
        or query_cost_per_1k > max_query_cost
    ):
        raise ValueError("acceptance report cost evidence failed verification")
    generated_at = report.get("generatedAt")
    if not isinstance(generated_at, str):
        raise ValueError("acceptance report timestamp is missing")
    try:
        observed_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("acceptance report timestamp is invalid") from exc
    now = datetime.now(timezone.utc)
    if (
        observed_at.tzinfo is None
        or observed_at > now + timedelta(minutes=5)
        or observed_at < now - timedelta(hours=args.max_report_age_hours)
    ):
        raise ValueError("acceptance report is expired or from the future")
    cost_attested_at = report.get("costAttestedAt")
    if not isinstance(cost_attested_at, str):
        raise ValueError("acceptance report cost timestamp is missing")
    try:
        attested_at = datetime.fromisoformat(cost_attested_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("acceptance report cost timestamp is invalid") from exc
    if (
        attested_at.tzinfo is None
        or attested_at < observed_at
        or attested_at > now + timedelta(minutes=5)
    ):
        raise ValueError("acceptance report cost timestamp is inconsistent")
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    args = parse_args()
    if (
        args.max_chunk_size is not None or args.chunk_overlap is not None
    ) and not args.chunking_strategy:
        raise ValueError("chunk size and overlap require --chunking-strategy")
    if args.command == "validate":
        release = load_release(args)
        print(
            json.dumps(
                {
                    "valid": True,
                    "documentCount": len(release.documents),
                    "contentVersion": release.index_version,
                },
                ensure_ascii=False,
            )
        )
        return 0

    index_name = require_confirmation(args)
    publisher = build_publisher(args)
    if args.command == "promote":
        report_sha256 = verify_acceptance_report(args, index_name)
        previous = publisher.switch_alias(index_name)
        print(
            json.dumps(
                {
                    "index": index_name,
                    "previousIndices": previous,
                    "aliasSwitched": True,
                    "acceptanceReportSha256": report_sha256,
                }
            )
        )
    elif args.command in {"stage", "publish"}:
        release = load_release(args)
        if not args.mapping.is_file() or args.mapping.stat().st_size > 1024 * 1024:
            raise ValueError("mapping must be a bounded JSON file")
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        if not isinstance(mapping, dict):
            raise ValueError("mapping must be a JSON object")
        if args.serverless:
            mapping = configure_serverless_index_definition(mapping)
        mappings = mapping.get("mappings")
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        semantic = properties.get("semantic_content") if isinstance(properties, dict) else None
        mapping_inference_id = semantic.get("inference_id") if isinstance(semantic, dict) else ""
        if (
            args.semantic_inference_id
            or args.semantic_search_inference_id
            or args.chunking_strategy
        ):
            inference_id = args.semantic_inference_id or mapping_inference_id
            if not isinstance(inference_id, str) or not inference_id:
                raise ValueError("semantic mapping requires an inference endpoint")
            mapping = configure_semantic_mapping(
                mapping,
                inference_id,
                search_inference_id=args.semantic_search_inference_id,
                chunking_strategy=args.chunking_strategy,
                max_chunk_size=args.max_chunk_size,
                chunk_overlap=args.chunk_overlap,
            )
        mappings = mapping.get("mappings")
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        semantic = properties.get("semantic_content") if isinstance(properties, dict) else None
        semantic_inference_id = semantic.get("inference_id") if isinstance(semantic, dict) else None
        receipt = (
            publisher.stage(index_name, release.documents, mapping)
            if args.command == "stage"
            else publisher.publish(index_name, release.documents, mapping)
        )
        print(
            json.dumps(
                {
                    "index": receipt.index_name,
                    "alias": receipt.alias,
                    "documentCount": receipt.document_count,
                    "previousIndices": receipt.previous_indices,
                    "semanticInferenceId": semantic_inference_id,
                    "semanticSearchInferenceId": receipt.search_inference_id,
                    "vectorChunkCount": receipt.vector_chunk_count,
                    "bulkBatches": receipt.bulk_batches,
                    "indexingDurationMs": round(receipt.indexing_duration_ms, 3),
                    "aliasSwitched": receipt.alias_switched,
                    "readyForEvaluation": not receipt.alias_switched,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "rollback":
        previous = publisher.switch_alias(index_name)
        print(json.dumps({"index": index_name, "previousIndices": previous}))
    else:
        publisher.delete_inactive_index(index_name)
        print(json.dumps({"deletedIndex": index_name}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
