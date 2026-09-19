from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from damai_agent.rag_eval import (
    load_eval_asset,
    validate_release_eval_governance,
)

CATALOG_SHA256 = "d" * 64


def approved_bundle(case_count: int = 100) -> dict[str, object]:
    cases: list[dict[str, object]] = []
    for position in range(case_count):
        dynamic = position >= case_count - 20
        case: dict[str, object] = {
            "case_id": f"case-{position:03d}",
            "query": f"业务验收问题 {position}",
            "tenant_id": "tenant-a",
            "category": "dynamic" if dynamic else "policy",
            "risk_level": "safety_critical" if position % 10 == 0 else "standard",
            "judgment_reference": f"label:judgment-{position:03d}",
        }
        if dynamic:
            case["must_block_as_dynamic"] = True
        else:
            case["expected_document_ids"] = [f"document-{position:03d}"]
        cases.append(case)
    return {
        "schema_version": "damai.rag.eval/v1",
        "dataset_id": "knowledge-production",
        "dataset_version": "2026.09.19",
        "catalog_sha256": CATALOG_SHA256,
        "owner_team": "search-quality",
        "approval_status": "approved",
        "approval_reference": "change:CHG-20260919-001",
        "approved_at": "2026-09-19T00:00:00Z",
        "reviewer_count": 2,
        "cases": cases,
    }


class RagEvalAssetTest(unittest.TestCase):
    def write_asset(self, directory: str, payload: object) -> Path:
        path = Path(directory) / "eval.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_approved_bundle_binds_catalog_and_emits_governance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = load_eval_asset(self.write_asset(directory, approved_bundle()))
        asset.require_release_eligible(
            minimum_case_count=100,
            expected_catalog_sha256=CATALOG_SHA256,
        )
        governance = asset.governance_payload(
            required=True,
            minimum_case_count=100,
            expected_catalog_sha256=CATALOG_SHA256,
        )
        validate_release_eval_governance(governance)
        self.assertTrue(governance["releaseEligible"])
        self.assertEqual(governance["categoryCounts"], {"dynamic": 20, "policy": 80})
        self.assertEqual(governance["judgmentCoverage"], 1.0)

    def test_release_rejects_wrong_catalog_and_legacy_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = load_eval_asset(self.write_asset(directory, approved_bundle()))
            with self.assertRaisesRegex(ValueError, "does not match"):
                asset.require_release_eligible(
                    minimum_case_count=100,
                    expected_catalog_sha256="e" * 64,
                )
            fixture = [
                {
                    "case_id": "fixture",
                    "query": "实名购票规则",
                    "tenant_id": "tenant-a",
                    "expected_document_ids": ["identity-policy"],
                }
            ]
            legacy = load_eval_asset(self.write_asset(directory, fixture))
            with self.assertRaisesRegex(ValueError, "versioned approved bundle"):
                legacy.require_release_eligible(
                    minimum_case_count=100,
                    expected_catalog_sha256=CATALOG_SHA256,
                )

    def test_bundle_rejects_duplicate_judgments(self) -> None:
        payload = approved_bundle()
        cases = payload["cases"]
        assert isinstance(cases, list)
        duplicate = dict(cases[0])
        duplicate["case_id"] = "duplicate-case"
        cases.append(duplicate)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "duplicate query judgments"):
                load_eval_asset(self.write_asset(directory, payload))


if __name__ == "__main__":
    unittest.main()
