"""Attest phase 4 SLO, trace, fault, and rollback evidence without embedding raw logs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from damai_agent.evidence import write_report
from damai_agent.phase4_slo import Phase4SloManifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--metrics-evidence", type=Path, required=True)
    parser.add_argument("--trace-evidence", type=Path, required=True)
    parser.add_argument("--fault-evidence", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    return parser.parse_args()


def bounded_bytes(path: Path, *, label: str, maximum: int = 20 * 1024 * 1024) -> bytes:
    resolved = path.resolve()
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= maximum:
        raise ValueError(f"{label} must be a bounded nonempty file")
    return resolved.read_bytes()


def main() -> int:
    args = parse_args()
    manifest_raw = bounded_bytes(args.manifest, label="SLO manifest", maximum=2 * 1024 * 1024)
    try:
        manifest_payload = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("SLO manifest must be valid UTF-8 JSON") from exc
    manifest = Phase4SloManifest.model_validate(manifest_payload)
    evidence_hashes = {
        "manifest": hashlib.sha256(manifest_raw).hexdigest(),
        "metrics": hashlib.sha256(
            bounded_bytes(args.metrics_evidence, label="metrics evidence")
        ).hexdigest(),
        "traces": hashlib.sha256(
            bounded_bytes(args.trace_evidence, label="trace evidence")
        ).hexdigest(),
        "faults": hashlib.sha256(
            bounded_bytes(args.fault_evidence, label="fault evidence")
        ).hexdigest(),
    }
    report = manifest.attestation_payload(evidence_hashes=evidence_hashes)
    write_report(args.report_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
