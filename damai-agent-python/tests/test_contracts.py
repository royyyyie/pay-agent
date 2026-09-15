from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from damai_agent.tools import JavaToolClient, ToolRegistry, build_java_tools

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ContractGenerationTest(unittest.TestCase):
    def test_generated_tool_schemas_are_up_to_date(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/generate_tool_schemas.py", "--check"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_registry_is_built_from_versioned_contract(self) -> None:
        client = JavaToolClient("http://127.0.0.1:6086", "test-key", 1)
        registry = ToolRegistry(build_java_tools(client))

        self.assertEqual(
            set(registry.names),
            {"search_programs", "get_program_detail", "list_ticket_categories"},
        )
        for spec in registry.specs:
            self.assertEqual(spec.version, "1.0.0")
            self.assertEqual(spec.risk, "READ_ONLY")
            self.assertEqual(spec.required_scope, "programs:read")
            self.assertFalse(spec.parameters["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
