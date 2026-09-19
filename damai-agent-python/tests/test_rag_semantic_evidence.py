from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.evaluate_rag import load_semantic_evidence


class RagSemanticEvidenceTest(unittest.TestCase):
    def report(self) -> dict[str, object]:
        return {
            "schemaVersion": "damai.rag.acceptance/v1",
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "targetIndex": "damai-knowledge-read-v-001",
            "indexVersion": "knowledge@sha256:abc",
            "retrievalProfile": "semantic_rerank",
            "semanticConfiguration": {
                "field": "semantic_content",
                "inferenceId": "embedding-v1",
                "searchInferenceId": "embedding-v1",
                "chunkingSettings": {
                    "strategy": "sentence",
                    "max_chunk_size": 200,
                    "sentence_overlap": 1,
                },
                "rerankInferenceId": "rerank-v1",
            },
            "passed": False,
        }

    def load(self, report: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            raw = json.dumps(report).encode()
            path.write_bytes(raw)
            configuration, provenance = load_semantic_evidence(
                path,
                target_index="damai-knowledge-read-v-001",
                index_version="knowledge@sha256:abc",
                semantic_field="semantic_content",
                retrieval_profile="semantic_rerank",
                rerank_inference_id="rerank-v1",
                max_age_hours=72,
            )
        self.assertEqual(provenance["reportSha256"], hashlib.sha256(raw).hexdigest())
        return configuration, provenance

    def test_failed_benchmark_can_supply_exact_mapping_evidence(self) -> None:
        configuration, provenance = self.load(self.report())
        self.assertEqual(configuration["inferenceId"], "embedding-v1")
        self.assertEqual(configuration["rerankInferenceId"], "rerank-v1")
        self.assertEqual(provenance["source"], "acceptance_report")

    def test_rejects_wrong_index_stale_or_mismatched_reranker(self) -> None:
        for mutation in ("index", "stale", "reranker"):
            with self.subTest(mutation=mutation):
                report = self.report()
                if mutation == "index":
                    report["targetIndex"] = "damai-knowledge-read-v-other"
                elif mutation == "stale":
                    report["generatedAt"] = (
                        datetime.now(timezone.utc) - timedelta(days=4)
                    ).isoformat()
                else:
                    semantic = report["semanticConfiguration"]
                    assert isinstance(semantic, dict)
                    semantic["rerankInferenceId"] = "rerank-v2"
                with self.assertRaises(ValueError):
                    self.load(report)

    def test_rejects_invalid_chunking_evidence(self) -> None:
        report = self.report()
        semantic = report["semanticConfiguration"]
        assert isinstance(semantic, dict)
        semantic["chunkingSettings"] = {
            "strategy": "sentence",
            "max_chunk_size": 1000,
            "sentence_overlap": 1,
        }
        with self.assertRaisesRegex(ValueError, "chunking"):
            self.load(report)


if __name__ == "__main__":
    unittest.main()
