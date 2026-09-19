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


def retriever(opener: FakeOpener, **options: object) -> ElasticsearchKnowledgeRetriever:
    return ElasticsearchKnowledgeRetriever(
        "https://es.example.com",
        "private-api-key",
        "damai-knowledge-read",
        "knowledge-2026.09.19",
        ("help.example.com",),
        opener=opener,  # type: ignore[arg-type]
        **options,  # type: ignore[arg-type]
    )


class ElasticsearchKnowledgeRetrieverTest(unittest.IsolatedAsyncioTestCase):
    async def test_acceptance_privileges_are_bounded_to_exact_index(self) -> None:
        opener = FakeOpener(
            {
                "has_all_requested": False,
                "cluster": {"monitor_inference": True},
                "index": {
                    "damai-knowledge-read": {
                        "read": True,
                        "view_index_metadata": False,
                    }
                },
            }
        )
        privileges = await retriever(opener).acceptance_privileges()
        self.assertEqual(
            privileges,
            {
                "monitorInference": True,
                "read": True,
                "viewIndexMetadata": False,
            },
        )
        request = opener.requests[0]
        self.assertTrue(request.full_url.endswith("/_security/user/_has_privileges"))
        body = json.loads(request.data or b"{}")
        self.assertEqual(body["cluster"], ["monitor_inference"])
        self.assertEqual(body["index"][0]["names"], ["damai-knowledge-read"])

    async def test_semantic_hybrid_uses_server_rrf_and_returns_bounded_passage(self) -> None:
        opener = FakeOpener(
            {
                "hits": {
                    "hits": [
                        {
                            "_score": 0.03,
                            "_source": source(),
                            "highlight": {"semantic_content": ["主办方规则", "完成信息核验"]},
                        }
                    ]
                }
            }
        )
        instance = retriever(opener, retrieval_profile="semantic-hybrid")
        hits = await instance.search(
            "观演人身份认证",
            tenant_id="tenant-a",
            locale="zh-CN",
            limit=4,
            moment=NOW,
        )
        self.assertEqual(instance.profile, "semantic-hybrid")
        self.assertEqual(hits[0].passage, "主办方规则 完成信息核验")
        body = json.loads(opener.requests[0].data or b"{}")
        self.assertNotIn("sort", body)
        rrf = body["retriever"]["rrf"]
        self.assertEqual(rrf["rank_window_size"], 50)
        self.assertEqual(rrf["rank_constant"], 60)
        self.assertEqual(len(rrf["retrievers"]), 2)
        semantic_query = rrf["retrievers"][1]["standard"]["query"]["bool"]
        self.assertIn(
            {"terms": {"tenant_id": ["tenant-a", "public"]}},
            semantic_query["filter"],
        )
        self.assertEqual(
            semantic_query["must"][0],
            {"match": {"semantic_content": {"query": "观演人身份认证"}}},
        )

    async def test_semantic_rerank_wraps_hybrid_retriever(self) -> None:
        opener = FakeOpener({"hits": {"hits": []}})
        instance = retriever(
            opener,
            retrieval_profile="semantic-rerank",
            rerank_inference_id="enterprise-reranker-v1",
            rank_window_size=80,
        )
        await instance.search(
            "实名规则",
            tenant_id="tenant-a",
            locale="zh-CN",
            limit=4,
            moment=NOW,
        )
        body = json.loads(opener.requests[0].data or b"{}")
        reranker = body["retriever"]["text_similarity_reranker"]
        self.assertEqual(reranker["inference_id"], "enterprise-reranker-v1")
        self.assertEqual(reranker["rank_window_size"], 80)
        self.assertIn("rrf", reranker["retriever"])

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

    async def test_semantic_readiness_exercises_embedding_endpoint(self) -> None:
        opener = FakeOpener({"hits": {"hits": []}})
        self.assertTrue(await retriever(opener, retrieval_profile="semantic-hybrid").check_ready())
        body = json.loads(opener.requests[0].data or b"{}")
        self.assertEqual(
            body["query"],
            {"match": {"semantic_content": "知识检索就绪检查"}},
        )

    async def test_semantic_configuration_is_read_from_exact_mapping(self) -> None:
        opener = FakeOpener(
            {
                "damai-knowledge-read": {
                    "mappings": {
                        "properties": {
                            "semantic_content": {
                                "type": "semantic_text",
                                "inference_id": "embedding-v1",
                                "search_inference_id": "embedding-query-v1",
                                "chunking_settings": {
                                    "strategy": "sentence",
                                    "max_chunk_size": 200,
                                    "sentence_overlap": 1,
                                },
                            }
                        }
                    }
                }
            }
        )
        instance = retriever(
            opener,
            retrieval_profile="semantic-rerank",
            rerank_inference_id="rerank-v1",
        )
        configuration = await instance.semantic_configuration()
        self.assertEqual(configuration["inferenceId"], "embedding-v1")
        self.assertEqual(configuration["searchInferenceId"], "embedding-query-v1")
        self.assertEqual(configuration["rerankInferenceId"], "rerank-v1")
        self.assertEqual(opener.requests[0].method, "GET")
        self.assertTrue(opener.requests[0].full_url.endswith("/_mapping"))

    async def test_readiness_probe_is_cached_to_bound_inference_cost(self) -> None:
        opener = FakeOpener({"hits": {"hits": []}})
        instance = retriever(opener, retrieval_profile="semantic-hybrid")
        self.assertTrue(await instance.check_ready())
        self.assertTrue(await instance.check_ready())
        self.assertEqual(len(opener.requests), 1)

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
        with self.assertRaisesRegex(ValueError, "requires an inference endpoint"):
            retriever(FakeOpener({}), retrieval_profile="semantic-rerank")
        with self.assertRaisesRegex(ValueError, "semantic field"):
            retriever(
                FakeOpener({}),
                retrieval_profile="semantic-hybrid",
                semantic_field="unsafe field",
            )
