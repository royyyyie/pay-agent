"""Verify a phase 7 attestation before enabling production canary traffic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from damai_agent.phase7_release import verify_phase7_attestation

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
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    for name in _EVIDENCE_ARGUMENTS:
        parser.add_argument(f"--{name}-evidence", type=Path, required=True)
    parser.add_argument("--max-age-hours", type=int, default=168)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_hash = verify_phase7_attestation(
        args.attestation,
        expected_commit=args.expected_commit,
        expected_image_digest=args.expected_image_digest,
        evidence_paths={name: getattr(args, f"{name}_evidence") for name in _EVIDENCE_ARGUMENTS},
        max_age_hours=args.max_age_hours,
    )
    print(
        json.dumps(
            {
                "verified": True,
                "attestationSha256": report_hash,
                "gitCommit": args.expected_commit,
                "imageDigest": args.expected_image_digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
