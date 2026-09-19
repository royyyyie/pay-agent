"""Read-only Elasticsearch adapter for stable knowledge retrieval."""

from __future__ import annotations

import asyncio
import json
import math
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Sequence
from urllib.parse import quote, urlparse

from .rag import KnowledgeDocument, KnowledgeHit

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_INDEX_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,254}")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._:@-]{1,128}")


class ElasticsearchRetrievalError(RuntimeError):
    """A sanitized, retryable retrieval failure without response bodies or credentials."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class ElasticsearchKnowledgeRetriever:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        index_alias: str,
        index_version: str,
        allowed_source_hosts: Sequence[str],
        *,
        timeout_seconds: float = 3.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Elasticsearch URL must be http/https with a host")
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Elasticsearch URL must not contain credentials or a path")
        if not api_key or len(api_key) > 8192 or any(char in api_key for char in "\r\n\0"):
            raise ValueError("Elasticsearch API key is required")
        if _INDEX_PATTERN.fullmatch(index_alias) is None or index_alias in {".", ".."}:
            raise ValueError("Elasticsearch index alias is invalid")
        if _VERSION_PATTERN.fullmatch(index_version) is None:
            raise ValueError("Elasticsearch knowledge version is invalid")
        if not allowed_source_hosts:
            raise ValueError("knowledge source host allowlist is required")
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("Elasticsearch timeout must be between 0.1 and 30 seconds")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._index_alias = index_alias
        self._index_version = index_version
        self._allowed_source_hosts = {host.lower() for host in allowed_source_hosts}
        self._timeout_seconds = timeout_seconds
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    @property
    def index_version(self) -> str:
        return self._index_version

    async def check_ready(self) -> bool:
        try:
            payload = await asyncio.to_thread(
                self._request,
                {
                    "size": 0,
                    "track_total_hits": False,
                    "query": {"match_none": {}},
                },
            )
        except ElasticsearchRetrievalError:
            return False
        return isinstance(payload.get("hits"), dict)

    async def search(
        self,
        query: str,
        *,
        tenant_id: str,
        locale: str,
        limit: int,
        moment: datetime,
    ) -> Sequence[KnowledgeHit]:
        if not query.strip() or not tenant_id or not 1 <= limit <= 20:
            return ()
        if moment.tzinfo is None:
            raise ValueError("retrieval time must include a timezone")
        timestamp = moment.isoformat()
        request_payload: dict[str, object] = {
            "size": limit,
            "track_total_hits": False,
            "timeout": f"{max(100, int(self._timeout_seconds * 1000))}ms",
            "_source": [
                "document_id",
                "version",
                "tenant_id",
                "locale",
                "category",
                "title",
                "content",
                "source",
                "effective_from",
                "effective_to",
            ],
            "sort": [{"_score": "desc"}, {"document_id": "asc"}, {"version": "desc"}],
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^3", "content"],
                                "type": "best_fields",
                                "minimum_should_match": "50%",
                            }
                        }
                    ],
                    "filter": [
                        {"terms": {"tenant_id": [tenant_id, "public"]}},
                        {"term": {"locale": locale}},
                        {"range": {"effective_from": {"lte": timestamp}}},
                        {
                            "bool": {
                                "should": [
                                    {"bool": {"must_not": {"exists": {"field": "effective_to"}}}},
                                    {"range": {"effective_to": {"gt": timestamp}}},
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ],
                }
            },
        }
        payload = await asyncio.to_thread(self._request, request_payload)
        hits_container = payload.get("hits")
        if not isinstance(hits_container, dict) or not isinstance(hits_container.get("hits"), list):
            raise ElasticsearchRetrievalError("Elasticsearch returned an invalid search envelope")
        results: list[KnowledgeHit] = []
        for raw_hit in hits_container["hits"]:
            if not isinstance(raw_hit, dict) or not isinstance(raw_hit.get("_source"), dict):
                raise ElasticsearchRetrievalError("Elasticsearch returned an invalid search hit")
            try:
                document = KnowledgeDocument.model_validate(raw_hit["_source"])
                score = float(raw_hit.get("_score") or 0)
            except (TypeError, ValueError) as exc:
                raise ElasticsearchRetrievalError(
                    "Elasticsearch returned invalid knowledge metadata"
                ) from exc
            source_host = (urlparse(document.source).hostname or "").lower()
            if (
                document.tenant_id not in {tenant_id, "public"}
                or document.locale.lower() != locale.lower()
                or not document.active_at(moment)
                or source_host not in self._allowed_source_hosts
                or not math.isfinite(score)
                or score < 0
            ):
                raise ElasticsearchRetrievalError(
                    "Elasticsearch result violated the knowledge isolation policy"
                )
            results.append(KnowledgeHit(document, score))
        return tuple(results)

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        target = (
            f"{self._base_url}/{quote(self._index_alias, safe='')}/_search"
            "?allow_partial_search_results=false"
        )
        request = urllib.request.Request(
            target,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"ApiKey {self._api_key}",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ElasticsearchRetrievalError("Elasticsearch knowledge search failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ElasticsearchRetrievalError("Elasticsearch response exceeded the size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ElasticsearchRetrievalError("Elasticsearch returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ElasticsearchRetrievalError("Elasticsearch returned an invalid response")
        return decoded
