"""Validate, publish, roll back, or clean up an Elasticsearch knowledge release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from damai_agent.knowledge_publish import (
    ElasticsearchKnowledgePublisher,
    configure_semantic_mapping,
)
from damai_agent.rag import load_knowledge_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING = PROJECT_ROOT / "docs" / "elasticsearch-knowledge-index.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "publish", "rollback", "delete"))
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--source-host", action="append", default=[])
    parser.add_argument("--url", default=os.environ.get("DAMAI_KNOWLEDGE_PUBLISH_URL", ""))
    parser.add_argument("--alias", default="damai-knowledge-read")
    parser.add_argument("--index")
    parser.add_argument("--confirm-index")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument(
        "--semantic-inference-id",
        default=os.environ.get("DAMAI_KNOWLEDGE_SEMANTIC_INFERENCE_ID", ""),
    )
    return parser.parse_args()


def load_release(args: argparse.Namespace):
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
    )


def main() -> int:
    args = parse_args()
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
    if args.command == "publish":
        release = load_release(args)
        if not args.mapping.is_file() or args.mapping.stat().st_size > 1024 * 1024:
            raise ValueError("mapping must be a bounded JSON file")
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        if not isinstance(mapping, dict):
            raise ValueError("mapping must be a JSON object")
        if args.semantic_inference_id:
            mapping = configure_semantic_mapping(mapping, args.semantic_inference_id)
        mappings = mapping.get("mappings")
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        semantic = properties.get("semantic_content") if isinstance(properties, dict) else None
        semantic_inference_id = semantic.get("inference_id") if isinstance(semantic, dict) else None
        receipt = publisher.publish(index_name, release.documents, mapping)
        print(
            json.dumps(
                {
                    "index": receipt.index_name,
                    "alias": receipt.alias,
                    "documentCount": receipt.document_count,
                    "previousIndices": receipt.previous_indices,
                    "semanticInferenceId": semantic_inference_id,
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
