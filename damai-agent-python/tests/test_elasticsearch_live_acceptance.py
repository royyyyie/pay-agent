from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from damai_agent.elasticsearch_rag import ElasticsearchKnowledgeRetriever

_REQUIRED = (
    "DAMAI_TEST_ELASTICSEARCH_URL",
    "DAMAI_TEST_ELASTICSEARCH_API_KEY",
    "DAMAI_TEST_ELASTICSEARCH_INDEX_ALIAS",
    "DAMAI_TEST_ELASTICSEARCH_INDEX_VERSION",
    "DAMAI_TEST_ELASTICSEARCH_SOURCE_HOST",
    "DAMAI_TEST_ELASTICSEARCH_QUERY",
    "DAMAI_TEST_ELASTICSEARCH_TENANT",
    "DAMAI_TEST_ELASTICSEARCH_EXPECTED_DOCUMENT_ID",
)


@unittest.skipUnless(all(os.environ.get(name) for name in _REQUIRED), "test Elasticsearch not set")
class ElasticsearchLiveAcceptanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_tenant_filtered_knowledge_search(self) -> None:
        retriever = ElasticsearchKnowledgeRetriever(
            os.environ["DAMAI_TEST_ELASTICSEARCH_URL"],
            os.environ["DAMAI_TEST_ELASTICSEARCH_API_KEY"],
            os.environ["DAMAI_TEST_ELASTICSEARCH_INDEX_ALIAS"],
            os.environ["DAMAI_TEST_ELASTICSEARCH_INDEX_VERSION"],
            (os.environ["DAMAI_TEST_ELASTICSEARCH_SOURCE_HOST"],),
        )
        self.assertTrue(await retriever.check_ready())
        hits = await retriever.search(
            os.environ["DAMAI_TEST_ELASTICSEARCH_QUERY"],
            tenant_id=os.environ["DAMAI_TEST_ELASTICSEARCH_TENANT"],
            locale=os.environ.get("DAMAI_TEST_ELASTICSEARCH_LOCALE", "zh-CN"),
            limit=4,
            moment=datetime.now(timezone.utc),
        )
        self.assertIn(
            os.environ["DAMAI_TEST_ELASTICSEARCH_EXPECTED_DOCUMENT_ID"],
            {hit.document.document_id for hit in hits},
        )
