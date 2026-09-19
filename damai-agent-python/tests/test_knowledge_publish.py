from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from damai_agent.knowledge_publish import (
    ElasticsearchKnowledgePublisher,
    KnowledgePublicationError,
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


def publisher(opener: FakeOpener) -> ElasticsearchKnowledgePublisher:
    return ElasticsearchKnowledgePublisher(
        "https://es.example.com",
        "publisher-secret",
        "damai-knowledge-read",
        opener=opener,  # type: ignore[arg-type]
    )


class KnowledgePublisherTest(unittest.TestCase):
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
