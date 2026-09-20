"""Guarded Elasticsearch knowledge publication and alias rollback."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import quote, urlparse

from .rag import InMemoryKnowledgeIndex, KnowledgeDocument

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_BULK_BYTES = 5 * 1024 * 1024
_MAX_BULK_DOCUMENTS = 500
_INDEX_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,254}")
_INFERENCE_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def configure_serverless_index_definition(
    index_definition: dict[str, object],
) -> dict[str, object]:
    """Remove topology settings managed by Elasticsearch Serverless."""

    configured = deepcopy(index_definition)
    settings = configured.get("settings")
    if settings is None:
        return configured
    if not isinstance(settings, dict):
        raise ValueError("knowledge index settings must be an object")
    settings.pop("number_of_shards", None)
    settings.pop("number_of_replicas", None)
    if not settings:
        configured.pop("settings")
    return configured


def configure_semantic_mapping(
    index_definition: dict[str, object],
    inference_id: str,
    *,
    semantic_field: str = "semantic_content",
    search_inference_id: str = "",
    chunking_strategy: str | None = None,
    max_chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict[str, object]:
    """Bind a semantic mapping to an audited endpoint without mutating the template."""

    if _INFERENCE_PATTERN.fullmatch(inference_id) is None:
        raise ValueError("semantic inference ID is invalid")
    configured = deepcopy(index_definition)
    mappings = configured.get("mappings")
    properties = mappings.get("properties") if isinstance(mappings, dict) else None
    semantic = properties.get(semantic_field) if isinstance(properties, dict) else None
    if not isinstance(semantic, dict) or semantic.get("type") != "semantic_text":
        raise ValueError("semantic mapping field is missing or invalid")
    semantic["inference_id"] = inference_id
    if search_inference_id:
        if _INFERENCE_PATTERN.fullmatch(search_inference_id) is None:
            raise ValueError("semantic search inference ID is invalid")
        semantic["search_inference_id"] = search_inference_id
    if chunking_strategy is not None:
        if chunking_strategy not in {"sentence", "word"}:
            raise ValueError("semantic chunking strategy is invalid")
        if max_chunk_size is None or not 20 <= max_chunk_size <= 500:
            raise ValueError("semantic chunk size must be between 20 and 500")
        if chunk_overlap is None or chunk_overlap < 0:
            raise ValueError("semantic chunk overlap is invalid")
        if chunking_strategy == "sentence":
            if chunk_overlap not in {0, 1}:
                raise ValueError("sentence chunk overlap must be 0 or 1")
            semantic["chunking_settings"] = {
                "strategy": "sentence",
                "max_chunk_size": max_chunk_size,
                "sentence_overlap": chunk_overlap,
            }
        else:
            if chunk_overlap > max_chunk_size // 2:
                raise ValueError("word chunk overlap cannot exceed half the chunk size")
            semantic["chunking_settings"] = {
                "strategy": "word",
                "max_chunk_size": max_chunk_size,
                "overlap": chunk_overlap,
            }
    return configured


class KnowledgePublicationError(RuntimeError):
    """Sanitized publication failure without response bodies or credentials."""


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


@dataclass(frozen=True, slots=True)
class KnowledgeReleaseReceipt:
    index_name: str
    alias: str
    document_count: int
    previous_indices: tuple[str, ...]
    semantic_inference_id: str = ""
    search_inference_id: str = ""
    vector_chunk_count: int | None = 0
    bulk_batches: int = 0
    indexing_duration_ms: float = 0.0
    alias_switched: bool = False
    deployment_mode: str = "stateful"


class ElasticsearchKnowledgePublisher:
    """Publishes immutable concrete indices and atomically moves one read alias."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        index_alias: str,
        *,
        timeout_seconds: float = 10.0,
        serverless: bool | None = False,
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
            raise ValueError("Elasticsearch publisher API key is required")
        self._validate_index(index_alias, label="alias")
        if not 0.1 <= timeout_seconds <= 60:
            raise ValueError("Elasticsearch timeout must be between 0.1 and 60 seconds")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._index_alias = index_alias
        self._managed_prefix = f"{index_alias}-v-"
        self._timeout_seconds = timeout_seconds
        self._serverless = serverless
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def is_serverless(self) -> bool:
        """Resolve and cache the deployment flavor without exposing credentials."""

        if self._serverless is not None:
            return self._serverless
        payload = self._request_json("GET", "/")
        version = payload.get("version") if payload is not None else None
        build_flavor = version.get("build_flavor") if isinstance(version, dict) else None
        if not isinstance(build_flavor, str) or not build_flavor:
            raise KnowledgePublicationError("Elasticsearch deployment flavor is unavailable")
        self._serverless = build_flavor == "serverless"
        return self._serverless

    def publish(
        self,
        index_name: str,
        documents: Sequence[KnowledgeDocument],
        index_definition: dict[str, object],
    ) -> KnowledgeReleaseReceipt:
        """Legacy atomic publication for lexical indices.

        Semantic indices must use ``stage`` followed by an externally evaluated
        ``switch_alias`` so an untested embedding model cannot become active.
        """

        if self._semantic_mapping(index_definition) is not None:
            raise ValueError("semantic knowledge releases must be staged before promotion")
        staged = self.stage(index_name, documents, index_definition)
        previous = self._replace_alias(index_name)
        return KnowledgeReleaseReceipt(
            index_name=staged.index_name,
            alias=staged.alias,
            document_count=staged.document_count,
            previous_indices=previous,
            bulk_batches=staged.bulk_batches,
            indexing_duration_ms=staged.indexing_duration_ms,
            alias_switched=True,
            deployment_mode=staged.deployment_mode,
        )

    def stage(
        self,
        index_name: str,
        documents: Sequence[KnowledgeDocument],
        index_definition: dict[str, object],
    ) -> KnowledgeReleaseReceipt:
        """Build and verify an immutable index without changing the read alias."""

        self._validate_managed_index(index_name)
        if not documents or len(documents) > 10_000:
            raise ValueError("knowledge release must contain documents")
        validated_release = InMemoryKnowledgeIndex(documents)
        documents = validated_release.documents
        mappings = index_definition.get("mappings")
        if (
            set(index_definition) - {"settings", "mappings"}
            or not isinstance(mappings, dict)
            or mappings.get("dynamic") != "strict"
        ):
            raise ValueError("knowledge index definition must contain strict mappings only")

        serverless = self.is_serverless()
        if serverless:
            index_definition = configure_serverless_index_definition(index_definition)
            mappings = index_definition.get("mappings")
            assert isinstance(mappings, dict)

        semantic = self._semantic_mapping(index_definition)
        inference_id = ""
        search_inference_id = ""
        inference_task_type = ""
        if semantic is not None:
            inference_id = self._mapping_inference_id(semantic, "inference_id")
            search_inference_id = self._mapping_inference_id(
                semantic, "search_inference_id", required=False
            )
            inference_task_type = self.verify_inference_endpoint(inference_id)
            if search_inference_id and search_inference_id != inference_id:
                self.verify_inference_endpoint(search_inference_id)

        created = self._request_json("PUT", f"/{quote(index_name, safe='')}", index_definition)
        if created is None or created.get("acknowledged") is not True:
            raise KnowledgePublicationError("Elasticsearch index creation was not acknowledged")
        started_at = time.perf_counter()
        batches = self._bulk_bodies(index_name, documents)
        for position, bulk_body in enumerate(batches, start=1):
            refresh = "wait_for" if position == len(batches) else "false"
            bulk_result = self._request_json(
                "POST",
                f"/_bulk?refresh={refresh}",
                bulk_body,
                content_type="application/x-ndjson",
            )
            if bulk_result is None or bulk_result.get("errors") is not False:
                raise KnowledgePublicationError("Elasticsearch bulk knowledge indexing failed")
        indexing_duration_ms = (time.perf_counter() - started_at) * 1000
        observed_count = self._index_count(index_name)
        if observed_count != len(documents):
            raise KnowledgePublicationError("Elasticsearch knowledge count verification failed")
        vector_chunk_count: int | None = 0
        if semantic is not None:
            vector_chunk_count = self._verify_semantic_index(
                index_name,
                semantic,
                document_count=observed_count,
                inference_task_type=inference_task_type,
            )
        return KnowledgeReleaseReceipt(
            index_name=index_name,
            alias=self._index_alias,
            document_count=observed_count,
            previous_indices=(),
            semantic_inference_id=inference_id,
            search_inference_id=search_inference_id or inference_id,
            vector_chunk_count=vector_chunk_count,
            bulk_batches=len(batches),
            indexing_duration_ms=indexing_duration_ms,
            alias_switched=False,
            deployment_mode="serverless" if serverless else "stateful",
        )

    def verify_inference_endpoint(self, inference_id: str) -> str:
        """Verify endpoint metadata and perform one real bounded embedding request."""

        if _INFERENCE_PATTERN.fullmatch(inference_id) is None:
            raise ValueError("semantic inference ID is invalid")
        payload = self._request_json("GET", f"/_inference/{quote(inference_id, safe='')}")
        endpoints = payload.get("endpoints") if payload is not None else None
        if not isinstance(endpoints, list) or len(endpoints) != 1:
            raise KnowledgePublicationError("Elasticsearch inference endpoint was not found")
        endpoint = endpoints[0]
        if not isinstance(endpoint, dict) or endpoint.get("inference_id") != inference_id:
            raise KnowledgePublicationError("Elasticsearch inference endpoint identity mismatch")
        raw_task_type = endpoint.get("task_type")
        if not isinstance(raw_task_type, str) or raw_task_type not in {
            "text_embedding",
            "sparse_embedding",
        }:
            raise KnowledgePublicationError("Elasticsearch inference endpoint task is incompatible")
        task_type = raw_task_type
        probe = self._request_json(
            "POST",
            f"/_inference/{task_type}/{quote(inference_id, safe='')}",
            {"input": ["大麦稳定知识语义索引发布探测"]},
        )
        result = probe.get(task_type) if probe is not None else None
        if not isinstance(result, list) or not result:
            raise KnowledgePublicationError("Elasticsearch inference endpoint probe failed")
        return task_type

    def switch_alias(self, target_index: str) -> tuple[str, ...]:
        self._validate_managed_index(target_index)
        if self._index_count(target_index) <= 0:
            raise KnowledgePublicationError("rollback target contains no knowledge documents")
        return self._replace_alias(target_index)

    def _replace_alias(self, target_index: str) -> tuple[str, ...]:
        previous = self.alias_indices()
        if previous == (target_index,):
            return previous
        actions: list[dict[str, object]] = [
            {
                "remove": {
                    "index": item,
                    "alias": self._index_alias,
                    "must_exist": True,
                }
            }
            for item in previous
        ]
        actions.append(
            {
                "add": {
                    "index": target_index,
                    "alias": self._index_alias,
                    "is_write_index": False,
                }
            }
        )
        switched = self._request_json("POST", "/_aliases", {"actions": actions})
        if (
            switched is None
            or switched.get("acknowledged") is not True
            or switched.get("errors") is True
        ):
            raise KnowledgePublicationError("Elasticsearch alias switch was not acknowledged")
        return previous

    def alias_indices(self) -> tuple[str, ...]:
        payload = self._request_json(
            "GET",
            f"/_alias/{quote(self._index_alias, safe='')}",
            allow_not_found=True,
        )
        if payload is None:
            return ()
        indices = tuple(sorted(payload))
        if any(not item.startswith(self._managed_prefix) for item in indices):
            raise KnowledgePublicationError("knowledge alias references an unmanaged index")
        return indices

    def delete_inactive_index(self, index_name: str) -> None:
        self._validate_managed_index(index_name)
        if index_name in self.alias_indices():
            raise KnowledgePublicationError("active knowledge index cannot be deleted")
        deleted = self._request_json("DELETE", f"/{quote(index_name, safe='')}")
        if deleted is None or deleted.get("acknowledged") is not True:
            raise KnowledgePublicationError("Elasticsearch index deletion was not acknowledged")

    def _index_count(self, index_name: str) -> int:
        payload = self._request_json("GET", f"/{quote(index_name, safe='')}/_count")
        if payload is None:
            raise KnowledgePublicationError("Elasticsearch returned an invalid document count")
        count = payload.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise KnowledgePublicationError("Elasticsearch returned an invalid document count")
        return count

    def _verify_semantic_index(
        self,
        index_name: str,
        expected_mapping: dict[str, object],
        *,
        document_count: int,
        inference_task_type: str,
    ) -> int | None:
        mapping_payload = self._request_json("GET", f"/{quote(index_name, safe='')}/_mapping")
        index_payload = mapping_payload.get(index_name) if mapping_payload is not None else None
        mappings = index_payload.get("mappings") if isinstance(index_payload, dict) else None
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        observed = properties.get("semantic_content") if isinstance(properties, dict) else None
        if not isinstance(observed, dict) or observed.get("type") != "semantic_text":
            raise KnowledgePublicationError("Elasticsearch semantic mapping verification failed")
        for key in ("inference_id", "search_inference_id", "chunking_settings"):
            expected = expected_mapping.get(key)
            if expected is not None and observed.get(key) != expected:
                raise KnowledgePublicationError(
                    "Elasticsearch semantic mapping verification failed"
                )

        vector_chunk_count: int | None = None
        if not self.is_serverless():
            stats = self._request_json(
                "GET",
                f"/{quote(index_name, safe='')}/_stats/docs,store"
                "?filter_path=indices.*.primaries.docs.count,indices.*.primaries.store.size_in_bytes",
            )
            indices = stats.get("indices") if stats is not None else None
            observed_stats = indices.get(index_name) if isinstance(indices, dict) else None
            primaries = (
                observed_stats.get("primaries") if isinstance(observed_stats, dict) else None
            )
            docs = primaries.get("docs") if isinstance(primaries, dict) else None
            lucene_count = docs.get("count") if isinstance(docs, dict) else None
            store = primaries.get("store") if isinstance(primaries, dict) else None
            store_bytes = store.get("size_in_bytes") if isinstance(store, dict) else None
            if (
                isinstance(lucene_count, bool)
                or not isinstance(lucene_count, int)
                or lucene_count < document_count * 2
                or isinstance(store_bytes, bool)
                or not isinstance(store_bytes, int)
                or store_bytes <= 0
            ):
                raise KnowledgePublicationError(
                    "Elasticsearch vector chunk storage verification failed"
                )
            vector_chunk_count = lucene_count - document_count

        smoke = self._request_json(
            "POST",
            f"/{quote(index_name, safe='')}/_search?allow_partial_search_results=false",
            {
                "size": 1,
                "track_total_hits": False,
                "query": {"match": {"semantic_content": "稳定知识检索规则"}},
            },
        )
        hits = smoke.get("hits") if smoke is not None else None
        hit_items = hits.get("hits") if isinstance(hits, dict) else None
        if (
            not isinstance(hit_items, list)
            or not hit_items
            or inference_task_type not in {"text_embedding", "sparse_embedding"}
        ):
            raise KnowledgePublicationError("Elasticsearch semantic search smoke test failed")
        return vector_chunk_count

    @staticmethod
    def _semantic_mapping(index_definition: dict[str, object]) -> dict[str, object] | None:
        mappings = index_definition.get("mappings")
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        semantic = properties.get("semantic_content") if isinstance(properties, dict) else None
        if semantic is None:
            return None
        if not isinstance(semantic, dict) or semantic.get("type") != "semantic_text":
            raise ValueError("semantic mapping field is invalid")
        chunking = semantic.get("chunking_settings")
        strategy = chunking.get("strategy") if isinstance(chunking, dict) else None
        chunk_size = chunking.get("max_chunk_size") if isinstance(chunking, dict) else None
        overlap_key = "sentence_overlap" if strategy == "sentence" else "overlap"
        overlap = chunking.get(overlap_key) if isinstance(chunking, dict) else None
        if (
            not isinstance(chunking, dict)
            or strategy not in {"sentence", "word"}
            or isinstance(chunk_size, bool)
            or not isinstance(chunk_size, int)
            or not 20 <= chunk_size <= 500
            or isinstance(overlap, bool)
            or not isinstance(overlap, int)
            or overlap < 0
            or (strategy == "sentence" and overlap not in {0, 1})
            or (strategy == "word" and overlap > chunk_size // 2)
        ):
            raise ValueError("semantic mapping must define an explicit chunking policy")
        return semantic

    @staticmethod
    def _mapping_inference_id(
        semantic: dict[str, object], key: str, *, required: bool = True
    ) -> str:
        value = semantic.get(key, "")
        if not value and not required:
            return ""
        if not isinstance(value, str) or _INFERENCE_PATTERN.fullmatch(value) is None:
            raise ValueError(f"semantic mapping {key} is invalid")
        return value

    def _bulk_bodies(
        self, index_name: str, documents: Sequence[KnowledgeDocument]
    ) -> tuple[bytes, ...]:
        batches: list[bytes] = []
        current = bytearray()
        current_documents = 0
        for document in documents:
            document_key = f"{document.tenant_id}:{document.document_id}:{document.version}"
            action = json.dumps(
                {"index": {"_index": index_name, "_id": document_key}},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            source = json.dumps(
                document.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry = f"{action}\n{source}\n".encode("utf-8")
            if len(entry) > _MAX_BULK_BYTES:
                raise ValueError("one knowledge document exceeds the bulk payload limit")
            if current and (
                len(current) + len(entry) > _MAX_BULK_BYTES
                or current_documents >= _MAX_BULK_DOCUMENTS
            ):
                batches.append(bytes(current))
                current = bytearray()
                current_documents = 0
            current.extend(entry)
            current_documents += 1
        if current:
            batches.append(bytes(current))
        return tuple(batches)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | bytes | None = None,
        *,
        content_type: str = "application/json",
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": content_type,
                "Authorization": f"ApiKey {self._api_key}",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise KnowledgePublicationError("Elasticsearch knowledge publication failed") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KnowledgePublicationError("Elasticsearch knowledge publication failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise KnowledgePublicationError("Elasticsearch response exceeded the size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise KnowledgePublicationError("Elasticsearch returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise KnowledgePublicationError("Elasticsearch returned an invalid response")
        return decoded

    def _validate_managed_index(self, index_name: str) -> None:
        self._validate_index(index_name, label="index")
        if not index_name.startswith(self._managed_prefix) or index_name == self._managed_prefix:
            raise ValueError("knowledge index is outside the managed alias prefix")

    @staticmethod
    def _validate_index(value: str, *, label: str) -> None:
        if _INDEX_PATTERN.fullmatch(value) is None or value in {".", ".."}:
            raise ValueError(f"Elasticsearch {label} is invalid")
