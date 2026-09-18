from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from damai_agent.generated.tool_models import REQUEST_MODELS, RESPONSE_MODELS
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
            self.assertEqual(spec.max_calls_per_turn, 3)
            self.assertTrue(spec.concurrency_safe)
            self.assertFalse(spec.exclusive)
            self.assertIs(spec.request_model, REQUEST_MODELS[spec.name])
            self.assertIs(spec.response_model, RESPONSE_MODELS[spec.name])

    def test_generated_request_model_applies_defaults_and_rejects_unknown_fields(self) -> None:
        request_model = REQUEST_MODELS["search_programs"]

        validated = request_model.model_validate({"keyword": "周杰伦"})

        self.assertEqual(validated.model_dump()["pageNumber"], 1)
        self.assertEqual(validated.model_dump()["pageSize"], 10)
        with self.assertRaises(ValidationError):
            request_model.model_validate({"keyword": None})
        with self.assertRaises(ValidationError):
            request_model.model_validate({"keyword": "周杰伦", "tenantId": "forged"})

    def test_generated_response_model_requires_contract_envelope(self) -> None:
        response_model = RESPONSE_MODELS["search_programs"]

        with self.assertRaises(ValidationError):
            response_model.model_validate(
                {
                    "requestId": "call-1",
                    "success": True,
                    "code": 0,
                    "message": "success",
                    "retryable": False,
                }
            )


if __name__ == "__main__":
    unittest.main()
