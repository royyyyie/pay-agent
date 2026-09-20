from __future__ import annotations

import io
import json
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from scripts.collect_elastic_billing import (
    ElasticBillingEvidenceError,
    collect_billing_evidence,
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
    def __init__(self, response: dict[str, object] | BaseException) -> None:
        self.response = response
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        self.requests.append(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return FakeResponse(self.response)


class ElasticBillingEvidenceTest(unittest.TestCase):
    def test_collects_one_project_without_persisting_cloud_credentials(self) -> None:
        opener = FakeOpener(
            {
                "total_ecu": 99,
                "instances": [
                    {
                        "id": "project-1",
                        "name": "damai-test",
                        "type": "projects",
                        "total_ecu": 12,
                        "product_line_items": [
                            {
                                "name": "Inference",
                                "sku": "serverless-inference",
                                "total_ecu": 7,
                            }
                        ],
                    },
                    {
                        "id": "project-2",
                        "name": "unrelated",
                        "type": "projects",
                        "total_ecu": 87,
                        "product_line_items": [],
                    },
                ],
            }
        )
        observed_now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)

        evidence = collect_billing_evidence(
            api_key="cloud-secret",
            organization_id="organization-1",
            project_id="project-1",
            from_time=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
            to_time=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
            opener=opener,  # type: ignore[arg-type]
            now=observed_now,
        )

        self.assertEqual(evidence["schemaVersion"], "damai.elastic.billing/v1")
        self.assertEqual(evidence["totalEcu"], 12)
        self.assertEqual(len(evidence["productLineItems"]), 1)
        self.assertNotIn("cloud-secret", json.dumps(evidence))
        self.assertNotIn("organization-1", json.dumps(evidence))
        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "ApiKey cloud-secret")
        self.assertIn("include_names=true", request.full_url)

    def test_rejects_missing_project_and_future_window(self) -> None:
        now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "timezones"):
            collect_billing_evidence(
                api_key="cloud-secret",
                organization_id="organization-1",
                project_id="project-1",
                from_time=datetime(2026, 9, 19, 10),
                to_time=datetime(2026, 9, 19, 11),
                opener=FakeOpener({"instances": []}),  # type: ignore[arg-type]
                now=now,
            )
        with self.assertRaisesRegex(ValueError, "future"):
            collect_billing_evidence(
                api_key="cloud-secret",
                organization_id="organization-1",
                project_id="project-1",
                from_time=now,
                to_time=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
                opener=FakeOpener({"instances": []}),  # type: ignore[arg-type]
                now=now,
            )
        with self.assertRaisesRegex(ElasticBillingEvidenceError, "uniquely"):
            collect_billing_evidence(
                api_key="cloud-secret",
                organization_id="organization-1",
                project_id="project-1",
                from_time=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
                to_time=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
                opener=FakeOpener({"instances": []}),  # type: ignore[arg-type]
                now=now,
            )

    def test_http_error_is_sanitized(self) -> None:
        backend_details = io.BytesIO(b'{"secret":"backend-details"}')
        error = urllib.error.HTTPError(
            "https://cloud.elastic.co",
            403,
            "forbidden",
            {},
            backend_details,
        )
        with self.assertRaisesRegex(ElasticBillingEvidenceError, "HTTP 403") as observed:
            collect_billing_evidence(
                api_key="cloud-secret",
                organization_id="organization-1",
                project_id="project-1",
                from_time=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
                to_time=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
                opener=FakeOpener(error),  # type: ignore[arg-type]
                now=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
            )
        self.assertNotIn("backend-details", str(observed.exception))
        self.assertNotIn("cloud-secret", str(observed.exception))
