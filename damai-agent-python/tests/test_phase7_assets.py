from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_script() -> ModuleType:
    script_path = ROOT / "scripts" / "load_readonly.py"
    spec = importlib.util.spec_from_file_location("load_readonly", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Phase7AssetsTest(unittest.TestCase):
    def test_container_is_non_root_and_uses_the_lockfile(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("uv sync --frozen", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", dockerfile)
        self.assertIn(
            "COPY --from=builder --chown=10001:10001 /opt/damai-agent/.venv ./.venv",
            dockerfile,
        )
        self.assertNotIn("USER root", dockerfile)

    def test_kubernetes_baseline_has_probes_resources_and_restricted_context(self) -> None:
        documents = list(
            yaml.safe_load_all(
                (ROOT / "deploy" / "kubernetes" / "base.yaml").read_text(encoding="utf-8")
            )
        )
        by_kind = {document["kind"]: document for document in documents}
        deployment = by_kind["Deployment"]["spec"]
        pod = deployment["template"]["spec"]
        container = pod["containers"][0]

        self.assertGreaterEqual(deployment["replicas"], 2)
        self.assertEqual(deployment["strategy"]["rollingUpdate"]["maxUnavailable"], 0)
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
        self.assertGreater(
            pod["terminationGracePeriodSeconds"],
            int(by_kind["ConfigMap"]["data"]["DAMAI_AGENT_SHUTDOWN_TIMEOUT_SECONDS"]),
        )
        self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/livez")
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/readyz")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertIn("ALL", container["securityContext"]["capabilities"]["drop"])
        self.assertIn("requests", container["resources"])
        self.assertIn("limits", container["resources"])
        self.assertIn("PodDisruptionBudget", by_kind)

    def test_load_probe_percentiles_are_nearest_rank(self) -> None:
        module = load_script()

        self.assertEqual(module.percentile([1.0, 2.0, 3.0, 100.0], 0.95), 100.0)
        self.assertEqual(module.percentile([], 0.95), 0.0)


if __name__ == "__main__":
    unittest.main()
