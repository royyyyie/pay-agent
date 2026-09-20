from __future__ import annotations

import unittest
from datetime import datetime, timezone

from damai_agent.rag_experiment import RagHumanReviewEvidence, compare_rag_experiment


def acceptance(profile: str, utilities: list[float]) -> dict[str, object]:
    return {
        "schemaVersion": "damai.rag.acceptance/v1",
        "targetIndex": "damai-knowledge-read-v-001",
        "evalSetSha256": "a" * 64,
        "retrievalProfile": profile,
        "evalGovernance": {
            "datasetId": "knowledge-production",
            "datasetVersion": "2026.09.20",
            "catalogSha256": "b" * 64,
            "caseCount": len(utilities),
            "releaseEligible": True,
        },
        "quality": {
            "passed": True,
            "failures": [],
            "caseResults": [
                {
                    "caseId": f"case-{position:03d}",
                    "passed": True,
                    "utilityScore": utility,
                }
                for position, utility in enumerate(utilities)
            ],
        },
    }


def review() -> RagHumanReviewEvidence:
    return RagHumanReviewEvidence(
        schema_version="damai.rag.human-review/v1",
        dataset_id="knowledge-production",
        dataset_version="2026.09.20",
        baseline_profile="semantic_hybrid",
        candidate_profile="semantic_rerank",
        review_protocol_version="blind-v1",
        blind_review=True,
        judged_case_count=100,
        reviewer_count=2,
        agreement_rate=0.9,
        safety_approved=True,
        approval_status="approved",
        approval_reference="change:CHG-001",
        approved_at=datetime.now(timezone.utc),
    )


class RagExperimentTest(unittest.TestCase):
    def compare(
        self,
        baseline_utilities: list[float],
        candidate_utilities: list[float],
        *,
        mode: str = "non_inferiority",
    ) -> dict[str, object]:
        return compare_rag_experiment(
            acceptance("semantic_hybrid", baseline_utilities),
            acceptance("semantic_rerank", candidate_utilities),
            review(),
            baseline_sha256="c" * 64,
            candidate_sha256="d" * 64,
            review_sha256="e" * 64,
            mode=mode,  # type: ignore[arg-type]
            bootstrap_samples=1_000,
        )

    def test_non_inferiority_accepts_tied_release_without_red_line_regression(self) -> None:
        report = self.compare([1.0] * 100, [1.0] * 100)
        self.assertTrue(report["passed"])
        statistics = report["statistics"]
        assert isinstance(statistics, dict)
        self.assertEqual(statistics["lowerConfidenceBound"], 0.0)
        self.assertEqual(statistics["redLineRegressions"], 0)

    def test_superiority_uses_paired_confidence_and_exact_sign_test(self) -> None:
        report = self.compare([0.8] * 100, [0.9] * 100, mode="superiority")
        self.assertTrue(report["passed"])
        statistics = report["statistics"]
        assert isinstance(statistics, dict)
        self.assertGreater(statistics["lowerConfidenceBound"], 0)
        self.assertLess(statistics["exactOneSidedSignPValue"], 0.05)

    def test_comparison_rejects_mismatched_governed_dataset(self) -> None:
        baseline = acceptance("semantic_hybrid", [1.0] * 100)
        candidate = acceptance("semantic_rerank", [1.0] * 100)
        governance = candidate["evalGovernance"]
        assert isinstance(governance, dict)
        governance["catalogSha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "different governed datasets"):
            compare_rag_experiment(
                baseline,
                candidate,
                review(),
                baseline_sha256="c" * 64,
                candidate_sha256="d" * 64,
                review_sha256="e" * 64,
                bootstrap_samples=1_000,
            )


if __name__ == "__main__":
    unittest.main()
