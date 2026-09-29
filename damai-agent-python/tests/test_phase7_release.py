from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from damai_agent.phase7_release import Phase7ReleaseManifest, verify_phase7_attestation

NOW = datetime(2026, 9, 29, 2, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
EVIDENCE_BYTES = b"phase-7-verified-evidence"
HASH = hashlib.sha256(EVIDENCE_BYTES).hexdigest()
EVIDENCE_NAMES = (
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


def valid_payload() -> dict[str, object]:
    fault = {
        "passed": True,
        "evidence_reference": "fault:verified",
        "recovery_time_seconds": 30,
        "duplicate_side_effects": 0,
        "blind_write_retries": 0,
        "cross_tenant_successes": 0,
    }
    rollback = {
        "passed": True,
        "evidence_reference": "rollback:verified",
        "data_loss_events": 0,
    }
    approval = {
        "approved": True,
        "approver_id": "reviewer.approved",
        "approval_reference": "change:approved",
        "approved_at": "2026-09-29T01:00:00Z",
    }
    return {
        "schema_version": "damai.phase7.release/v1",
        "environment_id": "staging-ap-southeast",
        "environment_tier": "staging",
        "git_commit": COMMIT,
        "image_repository": "ghcr.io/royyyyie/pay-agent/damai-agent",
        "image_digest": DIGEST,
        "configuration_sha256": HASH,
        "window_start": "2026-09-29T00:00:00Z",
        "window_end": "2026-09-29T00:30:00Z",
        "request_count": 1000,
        "peak_concurrency": 25,
        "success_rate": 0.999,
        "throughput_rps": 20,
        "p50_turn_latency_ms": 500,
        "p95_turn_latency_ms": 1500,
        "p99_turn_latency_ms": 3000,
        "trace_sample_count": 100,
        "trace_continuity_rate": 1,
        "supply_chain": {
            "sbom_generated": True,
            "provenance_attested": True,
            "immutable_base_images": True,
            "action_refs_pinned": True,
            "critical_vulnerabilities": 0,
            "high_vulnerabilities": 0,
            "secrets_detected": 0,
            "iac_critical_findings": 0,
            "iac_high_findings": 0,
        },
        "fault_scenarios": {
            name: copy.deepcopy(fault)
            for name in (
                "provider",
                "java_gateway",
                "redis",
                "postgresql",
                "elasticsearch",
                "kafka",
                "instance_kill",
            )
        },
        "rollback_drills": {
            name: copy.deepcopy(rollback)
            for name in ("application", "database", "knowledge_index", "backup_restore")
        },
        "safety_counters": {
            "unauthorized_tool_successes": 0,
            "cross_tenant_successes": 0,
            "duplicate_orders": 0,
            "blind_unknown_write_replays": 0,
            "pii_or_secret_leaks": 0,
        },
        "carryover_evidence_sha256": {
            "phase3": HASH,
            "phase4": HASH,
            "phase5": HASH,
            "phase6": HASH,
        },
        "approvals": {
            role: copy.deepcopy(approval) for role in ("security", "sre", "business", "transaction")
        },
        "canary_percentages": [1, 5, 25, 50, 100],
        "automatic_rollback_enabled": True,
    }


def evidence_hashes() -> dict[str, str]:
    return {name: HASH for name in EVIDENCE_NAMES}


def write_evidence(directory: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in EVIDENCE_NAMES:
        path = directory / f"{name}.json"
        path.write_bytes(EVIDENCE_BYTES)
        paths[name] = path
    return paths


class Phase7ReleaseTest(unittest.TestCase):
    def test_complete_evidence_passes_and_is_bound_to_commit_and_digest(self) -> None:
        manifest = Phase7ReleaseManifest.model_validate(valid_payload())
        report = manifest.attestation_payload(evidence_hashes=evidence_hashes(), now=NOW)

        self.assertTrue(report["passed"])
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["gitCommit"], COMMIT)
        self.assertEqual(report["imageDigest"], DIGEST)

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "phase7-attestation.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            report_hash = verify_phase7_attestation(
                path,
                expected_commit=COMMIT,
                expected_image_digest=DIGEST,
                evidence_paths=write_evidence(directory),
                now=NOW,
            )
        self.assertEqual(len(report_hash), 64)

    def test_security_fault_approval_and_carryover_fail_closed(self) -> None:
        payload = valid_payload()
        payload["supply_chain"]["high_vulnerabilities"] = 1  # type: ignore[index]
        payload["fault_scenarios"]["redis"]["blind_write_retries"] = 1  # type: ignore[index]
        payload["approvals"]["security"]["approved"] = False  # type: ignore[index]
        manifest = Phase7ReleaseManifest.model_validate(payload)
        hashes = evidence_hashes()
        hashes["phase6"] = "d" * 64

        report = manifest.attestation_payload(evidence_hashes=hashes, now=NOW)

        self.assertFalse(report["passed"])
        self.assertIn("SUPPLY_CHAIN_GATE_FAILED", report["failures"])
        self.assertIn("FAULT_INJECTION_GATE_FAILED", report["failures"])
        self.assertIn("APPROVAL_GATE_FAILED", report["failures"])
        self.assertIn("CARRYOVER_EVIDENCE_MISMATCH", report["failures"])

    def test_required_scenarios_and_canary_sequence_cannot_be_weakened(self) -> None:
        payload = valid_payload()
        del payload["fault_scenarios"]["kafka"]  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "fault scenario"):
            Phase7ReleaseManifest.model_validate(payload)

        payload = valid_payload()
        payload["canary_percentages"] = [10, 100]
        with self.assertRaisesRegex(ValidationError, "canary percentages"):
            Phase7ReleaseManifest.model_validate(payload)

    def test_verifier_rejects_a_tampered_passing_report(self) -> None:
        manifest = Phase7ReleaseManifest.model_validate(valid_payload())
        report = manifest.attestation_payload(evidence_hashes=evidence_hashes(), now=NOW)
        report["faultScenarios"]["provider"]["duplicate_side_effects"] = 1  # type: ignore[index]

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "tampered.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "failed verification"):
                verify_phase7_attestation(
                    path,
                    expected_commit=COMMIT,
                    expected_image_digest=DIGEST,
                    evidence_paths=write_evidence(directory),
                    now=NOW,
                )

    def test_verifier_rejects_evidence_file_tampering(self) -> None:
        manifest = Phase7ReleaseManifest.model_validate(valid_payload())
        report = manifest.attestation_payload(evidence_hashes=evidence_hashes(), now=NOW)

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "phase7-attestation.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            evidence_paths = write_evidence(directory)
            evidence_paths["metrics"].write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "release attestation"):
                verify_phase7_attestation(
                    path,
                    expected_commit=COMMIT,
                    expected_image_digest=DIGEST,
                    evidence_paths=evidence_paths,
                    now=NOW,
                )

    def test_example_manifest_is_valid_but_intentionally_not_release_eligible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        payload = json.loads(
            (root / "docs" / "phase7-release-manifest.example.json").read_text(encoding="utf-8")
        )
        manifest = Phase7ReleaseManifest.model_validate(payload)
        report = manifest.attestation_payload(evidence_hashes=evidence_hashes(), now=NOW)

        self.assertFalse(report["passed"])
        self.assertIn("INSUFFICIENT_REQUEST_COUNT", report["failures"])
        self.assertIn("APPROVAL_GATE_FAILED", report["failures"])


if __name__ == "__main__":
    unittest.main()
