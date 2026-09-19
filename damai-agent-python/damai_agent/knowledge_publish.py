"""Guarded Elasticsearch knowledge publication and alias rollback."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import quote, urlparse

from .rag import InMemoryKnowledgeIndex, KnowledgeDocument

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_BULK_BYTES = 12 * 1024 * 1024
_INDEX_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,254}")
_INFERENCE_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def configure_semantic_mapping(
    index_definition: dict[str, object],
    inference_id: str,
    *,
    semantic_field: str = "semantic_content",
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


class ElasticsearchKnowledgePublisher:
    """Publishes immutable concrete indices and atomically moves one read alias."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        index_alias: str,
        *,
        timeout_seconds: float = 10.0,
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
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def publish(
        self,
        index_name: str,
        documents: Sequence[KnowledgeDocument],
        index_definition: dict[str, object],
    ) -> KnowledgeReleaseReceipt:
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

        created = self._request_json("PUT", f"/{quote(index_name, safe='')}", index_definition)
        if created is None or created.get("acknowledged") is not True:
            raise KnowledgePublicationError("Elasticsearch index creation was not acknowledged")
        bulk_body = self._bulk_body(index_name, documents)
        bulk_result = self._request_json(
            "POST",
            "/_bulk?refresh=wait_for",
            bulk_body,
            content_type="application/x-ndjson",
        )
        if bulk_result is None:
            raise KnowledgePublicationError("Elasticsearch bulk knowledge indexing failed")
        if bulk_result.get("errors") is not False:
            raise KnowledgePublicationError("Elasticsearch bulk knowledge indexing failed")
        observed_count = self._index_count(index_name)
        if observed_count != len(documents):
            raise KnowledgePublicationError("Elasticsearch knowledge count verification failed")
        previous = self._replace_alias(index_name)
        return KnowledgeReleaseReceipt(
            index_name=index_name,
            alias=self._index_alias,
            document_count=observed_count,
            previous_indices=previous,
        )

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

    def _bulk_body(self, index_name: str, documents: Sequence[KnowledgeDocument]) -> bytes:
        lines: list[str] = []
        for document in documents:
            document_key = f"{document.tenant_id}:{document.document_id}:{document.version}"
            lines.append(
                json.dumps(
                    {"index": {"_index": index_name, "_id": document_key}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            lines.append(
                json.dumps(
                    document.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        body = ("\n".join(lines) + "\n").encode("utf-8")
        if len(body) > _MAX_BULK_BYTES:
            raise ValueError("knowledge bulk payload is too large")
        return body

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
