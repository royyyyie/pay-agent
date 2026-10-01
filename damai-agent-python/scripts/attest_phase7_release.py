"""Create a phase 7 release attestation bound to external evidence files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from damai_agent.evidence import write_report
from damai_agent.phase7_release import Phase7ReleaseManifest, hash_evidence_files

_EVIDENCE_ARGUMENTS = (
    "metrics",
    "traces",
    "faults",
    "rollback",
    "security",
    "approvals",
    "phase3",
    "phase4",
    "phase5",
    "phase6",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    for name in _EVIDENCE_ARGUMENTS:
        parser.add_argument(f"--{name}-evidence", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    return parser.parse_args()


def bounded_bytes(path: Path, *, label: str, maximum: int = 20 * 1024 * 1024) -> bytes:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} must be a bounded nonempty file")
    raw = resolved.read_bytes()
    if not 0 < len(raw) <= maximum:
        raise ValueError(f"{label} must be a bounded nonempty file")
    return raw


def main() -> int:
    args = parse_args()
    manifest_raw = bounded_bytes(args.manifest, label="phase 7 manifest", maximum=2 * 1024 * 1024)
    try:
        manifest_payload = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("phase 7 manifest must be valid UTF-8 JSON") from exc
    manifest = Phase7ReleaseManifest.model_validate(manifest_payload)
    evidence_hashes = hash_evidence_files(
        {name: getattr(args, f"{name}_evidence") for name in _EVIDENCE_ARGUMENTS}
    )
    report = manifest.attestation_payload(evidence_hashes=evidence_hashes)
    write_report(args.report_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
