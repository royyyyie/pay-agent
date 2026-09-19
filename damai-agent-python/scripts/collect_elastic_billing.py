"""Collect bounded Elastic Cloud billing evidence for one Serverless project."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from scripts.evaluate_rag import write_report

_BILLING_BASE_URL = "https://cloud.elastic.co"
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class ElasticBillingEvidenceError(RuntimeError):
    """Sanitized billing evidence failure without response bodies or credentials."""


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


def parse_timestamp(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def collect_billing_evidence(
    *,
    api_key: str,
    organization_id: str,
    project_id: str,
    from_time: datetime,
    to_time: datetime,
    timeout_seconds: float = 20.0,
    opener: urllib.request.OpenerDirector | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    if not api_key or len(api_key) > 8192 or any(char in api_key for char in "\r\n\0"):
        raise ValueError("Elastic Cloud API key is required")
    for label, value in (("organization", organization_id), ("project", project_id)):
        if _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError(f"Elastic Cloud {label} ID is invalid")
    if from_time.tzinfo is None or to_time.tzinfo is None:
        raise ValueError("billing evidence timestamps must include timezones")
    start = from_time.astimezone(timezone.utc)
    end = to_time.astimezone(timezone.utc)
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if end <= start or end - start > timedelta(days=31):
        raise ValueError("billing evidence window must be positive and at most 31 days")
    if end > observed_now + timedelta(minutes=5):
        raise ValueError("billing evidence window cannot be in the future")
    if not 0.1 <= timeout_seconds <= 60:
        raise ValueError("billing timeout must be between 0.1 and 60 seconds")

    query = urlencode(
        {
            "from": iso_z(start),
            "to": iso_z(end),
            "include_names": "true",
        }
    )
    url = (
        f"{_BILLING_BASE_URL}/api/v2/billing/organizations/"
        f"{quote(organization_id, safe='')}/costs/instances?{query}"
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"ApiKey {api_key}"},
    )
    client = opener or urllib.request.build_opener(_NoRedirect())
    try:
        with client.open(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ElasticBillingEvidenceError(
            f"Elastic Cloud billing request failed with HTTP {exc.code}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ElasticBillingEvidenceError("Elastic Cloud billing request failed") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ElasticBillingEvidenceError("Elastic Cloud billing response exceeded the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ElasticBillingEvidenceError(
            "Elastic Cloud billing response was invalid JSON"
        ) from exc
    instances = payload.get("instances") if isinstance(payload, dict) else None
    if not isinstance(instances, list) or len(instances) > 10_000:
        raise ElasticBillingEvidenceError("Elastic Cloud billing response was invalid")
    matching = [
        item for item in instances if isinstance(item, dict) and item.get("id") == project_id
    ]
    if len(matching) != 1:
        raise ElasticBillingEvidenceError(
            "Elastic Cloud billing project was not uniquely identified"
        )
    project = matching[0]
    total_ecu = project.get("total_ecu")
    line_items = project.get("product_line_items")
    project_name = project.get("name", "")
    instance_type = project.get("type", "")
    if (
        isinstance(total_ecu, bool)
        or not isinstance(total_ecu, (int, float))
        or not math.isfinite(float(total_ecu))
        or total_ecu < 0
        or not isinstance(line_items, list)
        or len(line_items) > 10_000
        or any(not isinstance(item, dict) for item in line_items)
        or not isinstance(project_name, str)
        or len(project_name) > 512
        or not isinstance(instance_type, str)
        or len(instance_type) > 128
    ):
        raise ElasticBillingEvidenceError("Elastic Cloud billing project evidence was invalid")

    organization_hash = hashlib.sha256(organization_id.encode("utf-8")).hexdigest()
    evidence_id = f"elastic-billing:{project_id}:{start.strftime('%Y%m%dT%H%M%SZ')}"
    return {
        "schemaVersion": "damai.elastic.billing/v1",
        "collectedAt": iso_z(observed_now),
        "sourceApi": "elastic-cloud-billing-v2",
        "sourcePayloadSha256": hashlib.sha256(raw).hexdigest(),
        "organizationIdSha256": organization_hash,
        "projectId": project_id,
        "projectName": project_name,
        "instanceType": instance_type,
        "from": iso_z(start),
        "to": iso_z(end),
        "totalEcu": total_ecu,
        "productLineItems": line_items,
        "costEvidence": evidence_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization-id",
        default=os.environ.get("ELASTIC_CLOUD_ORGANIZATION_ID", ""),
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--from-time", required=True)
    parser.add_argument("--to-time", required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("ELASTIC_CLOUD_API_KEY", "")
    evidence = collect_billing_evidence(
        api_key=api_key,
        organization_id=args.organization_id,
        project_id=args.project_id,
        from_time=parse_timestamp(args.from_time, label="billing from-time"),
        to_time=parse_timestamp(args.to_time, label="billing to-time"),
        timeout_seconds=args.timeout_seconds,
    )
    write_report(args.report_out, evidence)
    print(
        json.dumps(
            {
                "valid": True,
                "projectId": evidence["projectId"],
                "from": evidence["from"],
                "to": evidence["to"],
                "totalEcu": evidence["totalEcu"],
                "costEvidence": evidence["costEvidence"],
                "report": str(args.report_out.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
