from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from damai_agent.knowledge_publish import (
    ElasticsearchKnowledgePublisher,
    KnowledgePublicationError,
    configure_semantic_mapping,
    configure_serverless_index_definition,
)
from damai_agent.rag import KnowledgeCategory, KnowledgeDocument


def document(document_id: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=document_id,
        version="2026.09",
        tenant_id="public",
        locale="zh-CN",
        category=KnowledgeCategory.FAQ,
        title=f"规则 {document_id}",
        content="这是一段经过审核并允许发布的稳定知识正文。",
        source=f"https://help.example.com/{document_id}",
        effective_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, responses: list[dict[str, object] | BaseException]) -> None:
        self.responses = responses
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


def publisher(
    opener: FakeOpener, *, serverless: bool = False
) -> ElasticsearchKnowledgePublisher:
    return ElasticsearchKnowledgePublisher(
        "https://es.example.com",
        "publisher-secret",
        "damai-knowledge-read",
        serverless=serverless,
        opener=opener,  # type: ignore[arg-type]
    )


class KnowledgePublisherTest(unittest.TestCase):
    def test_serverless_definition_removes_only_managed_topology_settings(self) -> None:
        definition: dict[str, object] = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 1,
                "refresh_interval": "30s",
            },
            "mappings": {"dynamic": "strict", "properties": {}},
        }

        configured = configure_serverless_index_definition(definition)

        self.assertEqual(configured["settings"], {"refresh_interval": "30s"})
        self.assertEqual(
            definition["settings"],
            {
                "number_of_shards": 1,
                "number_of_replicas": 1,
                "refresh_interval": "30s",
            },
        )

    def test_semantic_mapping_uses_audited_multilingual_embedding_endpoint(self) -> None:
        mapping_path = (
            Path(__file__).parents[1] / "docs" / "elasticsearch-knowledge-index-semantic.json"
        )
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        properties = mapping["mappings"]["properties"]
        self.assertEqual(mapping["mappings"]["dynamic"], "strict")
        self.assertEqual(properties["title"]["copy_to"], "semantic_content")
        self.assertEqual(properties["content"]["copy_to"], "semantic_content")
        self.assertEqual(properties["semantic_content"]["type"], "semantic_text")
        self.assertEqual(
            properties["semantic_content"]["inference_id"],
            ".multilingual-e5-small-elasticsearch",
        )
        self.assertEqual(
            properties["semantic_content"]["chunking_settings"],
            {"strategy": "sentence", "max_chunk_size": 200, "sentence_overlap": 1},
        )
        configured = configure_semantic_mapping(mapping, "eis-multilingual-large-v1")
        self.assertEqual(
            configured["mappings"]["properties"]["semantic_content"]["inference_id"],
            "eis-multilingual-large-v1",
        )
        self.assertEqual(
            properties["semantic_content"]["inference_id"],
            ".multilingual-e5-small-elasticsearch",
        )

    def test_stage_rejects_unbounded_semantic_chunking_before_cloud_calls(self) -> None:
        mapping_path = (
            Path(__file__).parents[1] / "docs" / "elasticsearch-knowledge-index-semantic.json"
        )
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        mapping["mappings"]["properties"]["semantic_content"]["chunking_settings"][
            "max_chunk_size"
        ] = 5_000
        opener = FakeOpener([])
        with self.assertRaisesRegex(ValueError, "explicit chunking policy"):
            publisher(opener).stage(
                "damai-knowledge-read-v-invalid-chunks",
                (document("faq-1"),),
                mapping,
            )
        self.assertFalse(opener.requests)

    def test_stage_probes_embedding_and_verifies_hidden_vector_chunks_without_alias(self) -> None:
        mapping_path = (
            Path(__file__).parents[1] / "docs" / "elasticsearch-knowledge-index-semantic.json"
        )
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        index_name = "damai-knowledge-read-v-semantic-001"
        opener = FakeOpener(
            [
                {
                    "endpoints": [
                        {
                            "inference_id": ".multilingual-e5-small-elasticsearch",
                            "task_type": "text_embedding",
                        }
                    ]
                },
                {"text_embedding": [{"embedding": [0.1, 0.2]}]},
                {"acknowledged": True},
                {"errors": False, "items": []},
                {"count": 2},
                {index_name: {"mappings": mapping["mappings"]}},
                {
                    "indices": {
                        index_name: {
                            "primaries": {
                                "docs": {"count": 5},
                                "store": {"size_in_bytes": 4096},
                            }
                        }
                    }
                },
                {"hits": {"hits": [{"_id": "public:faq-1:2026.09"}]}},
            ]
        )
        receipt = publisher(opener).stage(
            index_name,
            (document("faq-1"), document("faq-2")),
            mapping,
        )

        self.assertFalse(receipt.alias_switched)
        self.assertEqual(receipt.vector_chunk_count, 3)
        self.assertEqual(receipt.semantic_inference_id, ".multilingual-e5-small-elasticsearch")
        self.assertEqual(receipt.bulk_batches, 1)
        urls = [request.full_url for request in opener.requests]
        self.assertIn(
            "https://es.example.com/_inference/text_embedding/.multilingual-e5-small-elasticsearch",
            urls,
        )
        self.assertNotIn("https://es.example.com/_aliases", urls)

    def test_serverless_stage_uses_semantic_search_instead_of_unavailable_stats(self) -> None:
        mapping_path = (
            Path(__file__).parents[1] / "docs" / "elasticsearch-knowledge-index-semantic.json"
        )
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        mapping = configure_serverless_index_definition(mapping)
        index_name = "damai-knowledge-read-v-serverless-001"
        opener = FakeOpener(
            [
                {
                    "endpoints": [
                        {
                            "inference_id": ".multilingual-e5-small-elasticsearch",
                            "task_type": "text_embedding",
                        }
                    ]
                },
                {"text_embedding": [{"embedding": [0.1, 0.2]}]},
                {"acknowledged": True},
                {"errors": False, "items": []},
                {"count": 1},
                {index_name: {"mappings": mapping["mappings"]}},
                {"hits": {"hits": [{"_id": "public:faq-1:2026.09"}]}},
            ]
        )

        receipt = publisher(opener, serverless=True).stage(
            index_name,
            (document("faq-1"),),
            mapping,
        )

        self.assertIsNone(receipt.vector_chunk_count)
        self.assertFalse(any("/_stats/" in request.full_url for request in opener.requests))

    def test_semantic_release_cannot_bypass_acceptance_with_legacy_publish(self) -> None:
        mapping_path = (
            Path(__file__).parents[1] / "docs" / "elasticsearch-knowledge-index-semantic.json"
        )
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "must be staged"):
            publisher(FakeOpener([])).publish(
                "damai-knowledge-read-v-semantic-unsafe",
                (document("faq-1"),),
                mapping,
            )

    def test_publish_verifies_count_and_atomically_switches_explicit_alias_targets(self) -> None:
        missing_alias = urllib.error.HTTPError("https://es.example.com", 404, "not found", {}, None)
        opener = FakeOpener(
            [
                {"acknowledged": True},
                {"errors": False, "items": []},
                {"count": 2},
                missing_alias,
                {"acknowledged": True},
            ]
        )
        release = publisher(opener).publish(
            "damai-knowledge-read-v-20260919-001",
            (document("faq-1"), document("faq-2")),
            {"mappings": {"dynamic": "strict", "properties": {}}},
        )

        self.assertEqual(release.document_count, 2)
        self.assertEqual(release.previous_indices, ())
        self.assertEqual(
            [(request.method, request.full_url) for request in opener.requests],
            [
                ("PUT", "https://es.example.com/damai-knowledge-read-v-20260919-001"),
                ("POST", "https://es.example.com/_bulk?refresh=wait_for"),
                (
                    "GET",
                    "https://es.example.com/damai-knowledge-read-v-20260919-001/_count",
                ),
                ("GET", "https://es.example.com/_alias/damai-knowledge-read"),
                ("POST", "https://es.example.com/_aliases"),
            ],
        )
        bulk_body = (opener.requests[1].data or b"").decode("utf-8")
        self.assertEqual(bulk_body.count("\n"), 4)
        self.assertNotIn("publisher-secret", bulk_body)
        alias_payload = json.loads(opener.requests[-1].data or b"{}")
        self.assertEqual(
            alias_payload["actions"],
            [
                {
                    "add": {
                        "index": "damai-knowledge-read-v-20260919-001",
                        "alias": "damai-knowledge-read",
                        "is_write_index": False,
                    }
                }
            ],
        )

    def test_stage_splits_large_releases_into_bounded_bulk_batches(self) -> None:
        documents = tuple(document(f"faq-{position}") for position in range(501))
        opener = FakeOpener(
            [
                {"acknowledged": True},
                {"errors": False, "items": []},
                {"errors": False, "items": []},
                {"count": len(documents)},
            ]
        )
        receipt = publisher(opener).stage(
            "damai-knowledge-read-v-batched",
            documents,
            {"mappings": {"dynamic": "strict", "properties": {}}},
        )
        self.assertEqual(receipt.bulk_batches, 2)
        bulk_urls = [request.full_url for request in opener.requests if "_bulk" in request.full_url]
        self.assertEqual(
            bulk_urls,
            [
                "https://es.example.com/_bulk?refresh=false",
                "https://es.example.com/_bulk?refresh=wait_for",
            ],
        )

    def test_rollback_removes_only_observed_indices_and_adds_exact_target(self) -> None:
        opener = FakeOpener(
            [
                {"count": 2},
                {"damai-knowledge-read-v-current": {"aliases": {"damai-knowledge-read": {}}}},
                {"acknowledged": True},
            ]
        )
        previous = publisher(opener).switch_alias("damai-knowledge-read-v-previous")
        self.assertEqual(previous, ("damai-knowledge-read-v-current",))
        payload = json.loads(opener.requests[-1].data or b"{}")
        self.assertEqual(
            payload["actions"],
            [
                {
                    "remove": {
                        "index": "damai-knowledge-read-v-current",
                        "alias": "damai-knowledge-read",
                        "must_exist": True,
                    }
                },
                {
                    "add": {
                        "index": "damai-knowledge-read-v-previous",
                        "alias": "damai-knowledge-read",
                        "is_write_index": False,
                    }
                },
            ],
        )
        self.assertNotIn("*", json.dumps(payload))

    def test_active_or_unmanaged_index_cannot_be_deleted(self) -> None:
        opener = FakeOpener(
            [{"damai-knowledge-read-v-current": {"aliases": {"damai-knowledge-read": {}}}}]
        )
        with self.assertRaisesRegex(KnowledgePublicationError, "cannot be deleted"):
            publisher(opener).delete_inactive_index("damai-knowledge-read-v-current")
        with self.assertRaisesRegex(ValueError, "managed alias prefix"):
            publisher(FakeOpener([])).delete_inactive_index("another-index")

    def test_bulk_error_is_sanitized_and_never_switches_alias(self) -> None:
        opener = FakeOpener(
            [
                {"acknowledged": True},
                {"errors": True, "private": "backend-details"},
            ]
        )
        with self.assertRaisesRegex(KnowledgePublicationError, "bulk knowledge") as observed:
            publisher(opener).publish(
                "damai-knowledge-read-v-failed",
                (document("faq-1"),),
                {"mappings": {"dynamic": "strict", "properties": {}}},
            )
        self.assertNotIn("backend-details", str(observed.exception))
        self.assertEqual(len(opener.requests), 2)
