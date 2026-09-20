from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from damai_agent.recommendation_eval import (
    evaluate_recommendations,
    load_recommendation_eval_asset,
)

CONTRACT_SHA256 = "c" * 64


def bundle() -> dict[str, object]:
    return {
        "schema_version": "damai.recommendation.eval/v1",
        "dataset_id": "recommendation-production",
        "dataset_version": "2026.09.20",
        "contract_sha256": CONTRACT_SHA256,
        "owner_team": "recommendation-quality",
        "approval_status": "approved",
        "approval_reference": "change:CHG-REC-001",
        "approved_at": "2026-09-20T00:00:00Z",
        "reviewer_count": 2,
        "cases": [
            {
                "case_id": f"recommend-{position:03d}",
                "user_text": f"帮我推荐 800 元以内的音乐剧，编号 {position}",
                "model_tool_name": "search_programs",
                "model_arguments": {"maxPrice": 1200},
                "expected_tool_name": "recommend_programs",
                "expected_max_price": 800,
                "category": "budget",
                "risk_level": "safety_critical" if position % 10 == 0 else "standard",
                "judgment_reference": f"label:recommend-{position:03d}",
            }
            for position in range(100)
        ],
    }


class RecommendationEvalGovernanceTest(unittest.TestCase):
    def test_approved_business_bundle_is_release_eligible_and_sliced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recommendation.json"
            path.write_text(json.dumps(bundle(), ensure_ascii=False), encoding="utf-8")
            asset = load_recommendation_eval_asset(path)
        asset.require_release_eligible(
            minimum_case_count=100,
            contract_sha256=CONTRACT_SHA256,
        )
        governance = asset.governance_payload(
            required=True,
            minimum_case_count=100,
            contract_sha256=CONTRACT_SHA256,
        )
        report = evaluate_recommendations(asset.cases).to_dict()
        self.assertTrue(governance["releaseEligible"])
        self.assertEqual(governance["judgmentCoverage"], 1.0)
        self.assertTrue(report["passedRedLines"])
        self.assertEqual(len(report["caseResults"]), 100)
        self.assertEqual(report["slices"]["category"]["budget"]["passed"], 100)

    def test_wrong_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recommendation.json"
            path.write_text(json.dumps(bundle(), ensure_ascii=False), encoding="utf-8")
            asset = load_recommendation_eval_asset(path)
        with self.assertRaisesRegex(ValueError, "not release eligible"):
            asset.require_release_eligible(
                minimum_case_count=100,
                contract_sha256="d" * 64,
            )


if __name__ == "__main__":
    unittest.main()
