from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.attest_rag_cost import main as attest_cost
from scripts.manage_knowledge_index import verify_acceptance_report


class RagReleaseGateTest(unittest.TestCase):
    def report(self, index_name: str) -> dict[str, object]:
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "schemaVersion": "damai.rag.acceptance/v1",
            "generatedAt": observed_at,
            "costAttestedAt": observed_at,
            "targetIndex": index_name,
            "retrievalProfile": "semantic_hybrid",
            "evalSetSha256": "a" * 64,
            "benchmarkReportSha256": "b" * 64,
            "benchmarkConfiguration": {
                "backend": "elasticsearch",
                "topK": 4,
                "candidateK": 12,
                "rankWindowSize": 20,
                "rrfRankConstant": 60,
                "repetitions": 10,
                "concurrency": 8,
                "warmupRequests": 10,
            },
            "semanticConfiguration": {
                "inferenceId": "embedding-v1",
                "chunkingSettings": {
                    "strategy": "sentence",
                    "max_chunk_size": 200,
                    "sentence_overlap": 1,
                },
            },
            "quality": {
                "passed": True,
                "recall": 0.95,
                "minRecall": 0.90,
                "meanReciprocalRank": 0.90,
                "minMeanReciprocalRank": 0.80,
                "citationPrecision": 0.80,
                "minCitationPrecision": 0.50,
                "citationIntegrityRate": 1.0,
                "dynamicBlockRate": 1.0,
                "p95LatencyMs": 500,
                "maxP95LatencyMs": 800,
                "failures": [],
            },
            "load": {
                "passed": True,
                "requestCount": 100,
                "minRequests": 100,
                "throughputQps": 20,
                "minThroughputQps": 10,
                "p95LatencyMs": 600,
                "maxP95LatencyMs": 800,
            },
            "cost": {
                "required": True,
                "passed": True,
                "evidence": "elastic-billing:usage-20260919",
                "evidenceSha256": "c" * 64,
                "observedIndexingCostMicroUsd": 420_000,
                "observedQueryCostMicroUsd": 80_000,
                "observedTotalCostMicroUsd": 500_000,
                "queryCostPer1kMicroUsd": 800_000,
                "maxTotalCostMicroUsd": 1_000_000,
                "maxQueryCostPer1kMicroUsd": 1_000_000,
            },
            "passed": True,
        }

    def test_promotion_requires_fresh_complete_report_for_exact_index(self) -> None:
        index_name = "damai-knowledge-read-v-semantic-001"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            path.write_text(json.dumps(self.report(index_name)), encoding="utf-8")
            digest = verify_acceptance_report(
                SimpleNamespace(acceptance_report=path, max_report_age_hours=72),
                index_name,
            )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_promotion_rejects_wrong_target_or_incomplete_cost_evidence(self) -> None:
        index_name = "damai-knowledge-read-v-semantic-001"
        for mutation in ("target", "cost"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                report = self.report(index_name)
                if mutation == "target":
                    report["targetIndex"] = "damai-knowledge-read-v-other"
                else:
                    report["cost"] = {"passed": False}
                path = Path(directory) / "acceptance.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "report"):
                    verify_acceptance_report(
                        SimpleNamespace(acceptance_report=path, max_report_age_hours=72),
                        index_name,
                    )

    def test_promotion_rejects_unbounded_or_missing_benchmark_configuration(self) -> None:
        index_name = "damai-knowledge-read-v-semantic-001"
        for mutation in ("missing", "undersized-window", "excessive-concurrency"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                report = self.report(index_name)
                benchmark = report["benchmarkConfiguration"]
                assert isinstance(benchmark, dict)
                if mutation == "missing":
                    report.pop("benchmarkConfiguration")
                elif mutation == "undersized-window":
                    benchmark["rankWindowSize"] = 10
                else:
                    benchmark["concurrency"] = 1000
                path = Path(directory) / "acceptance.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "benchmark evidence"):
                    verify_acceptance_report(
                        SimpleNamespace(acceptance_report=path, max_report_age_hours=72),
                        index_name,
                    )

    def test_cost_attestation_creates_new_chained_report(self) -> None:
        index_name = "damai-knowledge-read-v-semantic-001"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "benchmark.json"
            target = Path(directory) / "acceptance.json"
            evidence = Path(directory) / "billing.json"
            report = self.report(index_name)
            report["load"] = {"passed": True, "requestCount": 100}
            report["passed"] = False
            source.write_text(json.dumps(report), encoding="utf-8")
            evidence.write_text('{"observedCostUsd": 0.50}', encoding="utf-8")
            arguments = [
                "attest_rag_cost.py",
                "--benchmark-report",
                str(source),
                "--report-out",
                str(target),
                "--observed-indexing-cost-usd",
                "0.42",
                "--observed-query-cost-usd",
                "0.08",
                "--cost-evidence",
                "elastic-billing:usage-20260919",
                "--cost-evidence-file",
                str(evidence),
                "--max-total-cost-usd",
                "1.00",
                "--max-query-cost-per-1k-usd",
                "1.00",
            ]
            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(attest_cost(), 0)
            attested = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(attested["passed"])
        self.assertTrue(attested["cost"]["passed"])
        self.assertEqual(attested["cost"]["queryCostPer1kMicroUsd"], 800_000)
        self.assertRegex(attested["benchmarkReportSha256"], r"^[0-9a-f]{64}$")
