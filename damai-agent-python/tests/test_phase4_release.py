from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from damai_agent.phase4_release import verify_phase4_release_evidence
from damai_agent.phase4_slo import Phase4SloManifest

NOW = datetime.now(timezone.utc)
NOW_TEXT = NOW.isoformat()
INDEX_NAME = "damai-knowledge-read-v-001"
INDEX_VERSION = "knowledge@sha256:bbbbbbbbbbbbbbbb"


def acceptance() -> dict[str, object]:
    return {
        "schemaVersion": "damai.rag.acceptance/v1",
        "targetIndex": INDEX_NAME,
        "indexVersion": INDEX_VERSION,
        "retrievalProfile": "semantic_rerank",
        "evalSetSha256": "a" * 64,
        "benchmarkReportSha256": "d" * 64,
        "evalGovernance": {
            "datasetId": "knowledge-production",
            "datasetVersion": "2026.09.20",
            "caseCount": 100,
        },
    }


def experiment() -> dict[str, object]:
    return {
        "schemaVersion": "damai.rag.experiment/v1",
        "generatedAt": NOW_TEXT,
        "targetIndex": INDEX_NAME,
        "evalSetSha256": "a" * 64,
        "datasetId": "knowledge-production",
        "datasetVersion": "2026.09.20",
        "baselineProfile": "semantic_hybrid",
        "candidateProfile": "semantic_rerank",
        "baselineReportSha256": "c" * 64,
        "candidateReportSha256": "d" * 64,
        "reviewEvidenceSha256": "e" * 64,
        "statistics": {
            "mode": "non_inferiority",
            "caseCount": 100,
            "lowerConfidenceBound": 0.0,
            "confidence": 0.95,
            "nonInferiorityMargin": 0.02,
            "exactOneSidedSignPValue": 1.0,
            "redLineRegressions": 0,
            "passed": True,
        },
        "humanReview": {
            "blind": True,
            "judgedCaseCount": 100,
            "reviewerCount": 2,
            "agreementRate": 0.9,
            "safetyApproved": True,
            "approvalReference": "change:CHG-RAG-001",
            "approvedAt": NOW_TEXT,
            "passed": True,
        },
        "passed": True,
    }


def recommendation() -> dict[str, object]:
    return {
        "schemaVersion": "damai.recommendation.acceptance/v1",
        "generatedAt": NOW_TEXT,
        "contractSha256": "f" * 64,
        "evalGovernance": {
            "required": True,
            "schemaVersion": "damai.recommendation.eval/v1",
            "approved": True,
            "approvalReference": "change:CHG-REC-001",
            "approvedAt": NOW_TEXT,
            "reviewerCount": 2,
            "contractMatchVerified": True,
            "releaseEligible": True,
            "judgmentCoverage": 1.0,
            "contractSha256": "f" * 64,
            "caseCount": 100,
            "categoryCounts": {"budget": 100},
            "riskLevelCounts": {"standard": 90, "safety_critical": 10},
        },
        "quality": {
            "passedRedLines": True,
            "routingAccuracy": 1.0,
            "budgetEnforcementRate": 1.0,
            "preferenceAccuracy": 1.0,
            "liveVerificationRate": 1.0,
            "failures": [],
            "caseResults": [{"caseId": f"case-{value}"} for value in range(100)],
        },
        "passed": True,
    }


def slo() -> dict[str, object]:
    faults = {
        name: {
            "passed": True,
            "evidenceReference": f"fault:{name}",
            "recoveryTimeMs": 1000,
            "duplicateSideEffects": 0,
        }
        for name in ("provider", "java_gateway", "redis", "postgresql")
    }
    return {
        "schemaVersion": "damai.phase4.slo-attestation/v1",
        "generatedAt": NOW_TEXT,
        "environmentId": "phase4-test",
        "environmentTier": "test",
        "deploymentVersion": "agent-2026.09.20",
        "knowledgeIndexVersion": INDEX_VERSION,
        "windowStart": (NOW - timedelta(minutes=20)).isoformat(),
        "windowEnd": (NOW - timedelta(minutes=5)).isoformat(),
        "requestCount": 1000,
        "availability": 0.999,
        "p95TurnLatencyMs": 1000,
        "traceSampleCount": 50,
        "traceContinuityRate": 1.0,
        "ragJavaTraceSampleCount": 20,
        "faultScenarios": faults,
        "rollbackDrillPassed": True,
        "operationsApproved": True,
        "securityApproved": True,
        "reviewerCount": 2,
        "approvalReference": "change:CHG-SLO-001",
        "approvedAt": NOW_TEXT,
        "evidenceSha256": {
            "manifest": "1" * 64,
            "metrics": "2" * 64,
            "traces": "3" * 64,
            "faults": "4" * 64,
        },
        "passed": True,
    }


class Phase4ReleaseTest(unittest.TestCase):
    def write(self, directory: str, name: str, payload: object) -> Path:
        path = Path(directory) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_final_gate_requires_all_four_chained_evidence_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hashes = verify_phase4_release_evidence(
                acceptance_report=self.write(directory, "acceptance.json", acceptance()),
                experiment_report=self.write(directory, "experiment.json", experiment()),
                recommendation_report=self.write(
                    directory, "recommendation.json", recommendation()
                ),
                slo_report=self.write(directory, "slo.json", slo()),
                index_name=INDEX_NAME,
                max_age_hours=72,
            )
        self.assertEqual(
            set(hashes),
            {
                "acceptanceReportSha256",
                "experimentReportSha256",
                "recommendationReportSha256",
                "sloReportSha256",
            },
        )

    def test_final_gate_rejects_missing_fault_scenario(self) -> None:
        slo_report = slo()
        faults = slo_report["faultScenarios"]
        assert isinstance(faults, dict)
        faults.pop("redis")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "SLO, trace, fault"):
                verify_phase4_release_evidence(
                    acceptance_report=self.write(directory, "acceptance.json", acceptance()),
                    experiment_report=self.write(directory, "experiment.json", experiment()),
                    recommendation_report=self.write(
                        directory, "recommendation.json", recommendation()
                    ),
                    slo_report=self.write(directory, "slo.json", slo_report),
                    index_name=INDEX_NAME,
                    max_age_hours=72,
                )

    def test_slo_manifest_fails_closed_below_availability_target(self) -> None:
        manifest = {
            "schema_version": "damai.phase4.slo/v1",
            "environment_id": "phase4-test",
            "environment_tier": "test",
            "deployment_version": "agent-2026.09.20",
            "knowledge_index_version": INDEX_VERSION,
            "window_start": (NOW - timedelta(minutes=20)).isoformat(),
            "window_end": (NOW - timedelta(minutes=5)).isoformat(),
            "request_count": 1000,
            "availability": 0.98,
            "p95_turn_latency_ms": 1000,
            "trace_sample_count": 50,
            "trace_continuity_rate": 1.0,
            "rag_java_trace_sample_count": 20,
            "fault_scenarios": {
                name: {
                    "passed": True,
                    "evidence_reference": f"fault:{name}",
                    "recovery_time_ms": 1000,
                    "duplicate_side_effects": 0,
                }
                for name in ("provider", "java_gateway", "redis", "postgresql")
            },
            "rollback_drill_passed": True,
            "operations_approved": True,
            "security_approved": True,
            "reviewer_count": 2,
            "approval_status": "approved",
            "approval_reference": "change:CHG-SLO-001",
            "approved_at": NOW_TEXT,
        }
        evidence = Phase4SloManifest.model_validate(manifest)
        self.assertFalse(evidence.release_eligible(now=NOW + timedelta(seconds=1)))


if __name__ == "__main__":
    unittest.main()
