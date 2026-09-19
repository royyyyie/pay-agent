from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from damai_agent.elasticsearch_rag import (
    ElasticsearchKnowledgeRetriever,
    ElasticsearchRetrievalError,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def source(*, tenant_id: str = "tenant-a", source_host: str = "help.example.com") -> dict[str, Any]:
    return {
        "document_id": "identity-policy",
        "version": "2026.09",
        "tenant_id": tenant_id,
        "locale": "zh-CN",
        "category": "identity_policy",
        "title": "实名规则",
        "content": "实名制购票时应按照主办方公布的规则完成信息核验。",
        "source": f"https://{source_host}/identity-policy",
        "effective_from": "2026-09-01T00:00:00+00:00",
        "effective_to": None,
    }


class FakeResponse:
    def __init__(self, payload: dict[str, object] | bytes) -> None:
        self.payload = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, payload: dict[str, object] | bytes | BaseException) -> None:
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        if isinstance(self.payload, BaseException):
            raise self.payload
        return FakeResponse(self.payload)


def retriever(opener: FakeOpener) -> ElasticsearchKnowledgeRetriever:
    return ElasticsearchKnowledgeRetriever(
        "https://es.example.com",
        "private-api-key",
        "damai-knowledge-read",
        "knowledge-2026.09.19",
        ("help.example.com",),
        opener=opener,  # type: ignore[arg-type]
    )


class ElasticsearchKnowledgeRetrieverTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_fixed_alias_auth_and_server_side_isolation_filters(self) -> None:
        opener = FakeOpener({"hits": {"hits": [{"_score": 2.5, "_source": source()}]}})
        hits = await retriever(opener).search(
            "实名购票规则",
            tenant_id="tenant-a",
            locale="zh-CN",
            limit=4,
            moment=NOW,
        )
        self.assertEqual(hits[0].document.document_id, "identity-policy")
        request = opener.requests[0]
        self.assertEqual(
            request.full_url,
            "https://es.example.com/damai-knowledge-read/_search"
            "?allow_partial_search_results=false",
        )
        self.assertEqual(request.get_header("Authorization"), "ApiKey private-api-key")
        body = json.loads(request.data or b"{}")
        filters = body["query"]["bool"]["filter"]
        lexical_channels = body["query"]["bool"]["should"]
        self.assertEqual(body["query"]["bool"]["minimum_should_match"], 1)
        self.assertEqual(len(lexical_channels), 3)
        self.assertIn("multi_match", lexical_channels[0])
        self.assertIn("match_phrase", lexical_channels[1])
        self.assertIn({"terms": {"tenant_id": ["tenant-a", "public"]}}, filters)
        self.assertIn({"term": {"locale": "zh-CN"}}, filters)
        self.assertEqual(body["size"], 4)
        self.assertEqual(
            body["sort"],
            [{"_score": "desc"}, {"document_id": "asc"}, {"version": "desc"}],
        )
        self.assertNotIn("private-api-key", json.dumps(body))

    async def test_client_revalidates_tenant_source_and_effective_window(self) -> None:
        cases = (
            source(tenant_id="tenant-b"),
            source(source_host="untrusted.example.com"),
            {**source(), "effective_to": "2026-09-10T00:00:00+00:00"},
        )
        for item in cases:
            with self.subTest(item=item):
                instance = retriever(
                    FakeOpener({"hits": {"hits": [{"_score": 1, "_source": item}]}})
                )
                with self.assertRaisesRegex(ElasticsearchRetrievalError, "isolation policy"):
                    await instance.search(
                        "实名规则",
                        tenant_id="tenant-a",
                        locale="zh-CN",
                        limit=4,
                        moment=NOW,
                    )

    async def test_http_error_is_sanitized_and_readiness_fails_closed(self) -> None:
        error = urllib.error.HTTPError("https://es.example.com", 503, "private response", {}, None)
        instance = retriever(FakeOpener(error))
        with self.assertRaisesRegex(ElasticsearchRetrievalError, "search failed") as observed:
            await instance.search(
                "实名规则",
                tenant_id="tenant-a",
                locale="zh-CN",
                limit=4,
                moment=NOW,
            )
        self.assertNotIn("private response", str(observed.exception))
        self.assertFalse(await instance.check_ready())

    async def test_ready_uses_a_zero_hit_search(self) -> None:
        opener = FakeOpener({"hits": {"hits": []}})
        self.assertTrue(await retriever(opener).check_ready())
        body = json.loads(opener.requests[0].data or b"{}")
        self.assertEqual(body["size"], 0)
        self.assertEqual(body["query"], {"match_none": {}})

    def test_rejects_unsafe_endpoint_alias_and_missing_key(self) -> None:
        for endpoint, key, alias in (
            ("https://user:pass@es.example.com", "key", "safe"),
            ("https://es.example.com/path", "key", "safe"),
            ("https://es.example.com?index=other", "key", "safe"),
            ("https://es.example.com", "", "safe"),
            ("https://es.example.com", "key\r\nX-Forged: value", "safe"),
            ("https://es.example.com", "key", "knowledge-*"),
        ):
            with self.subTest(endpoint=endpoint, alias=alias):
                with self.assertRaises(ValueError):
                    ElasticsearchKnowledgeRetriever(
                        endpoint,
                        key,
                        alias,
                        "version-1",
                        ("help.example.com",),
                    )
