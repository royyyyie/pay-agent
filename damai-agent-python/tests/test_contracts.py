from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from damai_agent.api import build_runner
from damai_agent.config import Settings
from damai_agent.generated.tool_models import REQUEST_MODELS, RESPONSE_MODELS
from damai_agent.tools import JavaToolClient, ToolRegistry, build_java_tools

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_EXAMPLES = PROJECT_ROOT.parent / "contracts" / "examples" / "agent-tools-v1"


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
            {
                "search_programs",
                "recommend_programs",
                "get_program_detail",
                "list_ticket_categories",
                "create_watch_rule",
                "update_watch_rule",
                "set_watch_rule_status",
                "list_watch_rules",
                "prepare_purchase_intent",
                "get_purchase_intent",
                "cancel_purchase_intent",
                "list_purchase_attendees",
                "submit_confirmed_order",
            },
        )
        for spec in registry.specs:
            self.assertEqual(spec.version, "1.3.0")
            self.assertFalse(spec.parameters["additionalProperties"])
            self.assertIs(spec.request_model, REQUEST_MODELS[spec.name])
            self.assertIs(spec.response_model, RESPONSE_MODELS[spec.name])
            if spec.name in {
                "create_watch_rule",
                "update_watch_rule",
                "set_watch_rule_status",
            }:
                self.assertEqual(spec.risk, "REVERSIBLE_WRITE")
                self.assertEqual(spec.required_scope, "watch:write")
                self.assertEqual(spec.max_calls_per_turn, 1)
                self.assertFalse(spec.concurrency_safe)
                self.assertTrue(spec.exclusive)
            elif spec.name == "list_watch_rules":
                self.assertEqual(spec.risk, "READ_ONLY")
                self.assertEqual(spec.required_scope, "watch:read")
                self.assertTrue(spec.concurrency_safe)
            elif spec.name in {"prepare_purchase_intent", "cancel_purchase_intent"}:
                self.assertEqual(spec.risk, "REVERSIBLE_WRITE")
                self.assertEqual(spec.required_scope, "purchase:intent:write")
                self.assertEqual(spec.max_calls_per_turn, 1)
                self.assertFalse(spec.concurrency_safe)
                self.assertTrue(spec.exclusive)
            elif spec.name in {"get_purchase_intent", "list_purchase_attendees"}:
                self.assertEqual(spec.risk, "READ_ONLY")
                self.assertEqual(spec.required_scope, "purchase:intent:read")
                self.assertTrue(spec.concurrency_safe)
            elif spec.name == "submit_confirmed_order":
                self.assertEqual(spec.risk, "ORDER_WRITE")
                self.assertEqual(spec.required_scope, "order:submit")
                self.assertEqual(spec.max_calls_per_turn, 1)
                self.assertFalse(spec.concurrency_safe)
                self.assertTrue(spec.exclusive)
            else:
                self.assertEqual(spec.risk, "READ_ONLY")
                self.assertEqual(spec.required_scope, "programs:read")
                self.assertEqual(spec.max_calls_per_turn, 3)
                self.assertTrue(spec.concurrency_safe)
                self.assertFalse(spec.exclusive)

    def test_purchase_intent_tools_are_hidden_until_explicitly_enabled(self) -> None:
        default_runner = build_runner(Settings())
        self.assertNotIn("prepare_purchase_intent", default_runner.tool_names)
        self.assertNotIn("create_watch_rule", default_runner.tool_names)

        enabled_runner = build_runner(
            Settings(
                runtime_backend="durable",
                postgres_dsn="postgresql://user:pass@db.example/test",
                redis_url="rediss://cache.example:6379/0",
                delegation_hmac_key="d" * 32,
                persist_tool_audit=True,
                purchase_intents_enabled=True,
            )
        )
        self.assertIn("prepare_purchase_intent", enabled_runner.tool_names)
        self.assertIn("get_purchase_intent", enabled_runner.tool_names)
        self.assertIn("cancel_purchase_intent", enabled_runner.tool_names)
        self.assertIn("list_purchase_attendees", enabled_runner.tool_names)
        self.assertNotIn("submit_confirmed_order", enabled_runner.tool_names)
        self.assertNotIn("create_watch_rule", enabled_runner.tool_names)

        order_runner = build_runner(
            Settings(
                runtime_backend="durable",
                postgres_dsn="postgresql://user:pass@db.example/test",
                redis_url="rediss://cache.example:6379/0",
                delegation_hmac_key="d" * 32,
                persist_tool_audit=True,
                purchase_intents_enabled=True,
                order_submission_enabled=True,
            )
        )
        self.assertIn("submit_confirmed_order", order_runner.tool_names)

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

    def test_purchase_intent_contract_never_accepts_price_or_confirmation(self) -> None:
        prepare_model = REQUEST_MODELS["prepare_purchase_intent"]
        prepared = prepare_model.model_validate(
            {
                "programId": 1001,
                "ticketCategoryId": 3001,
                "quantity": 1,
                "ticketUserIds": [9001],
            }
        )
        self.assertEqual(prepared.quantity, 1)
        with self.assertRaises(ValidationError):
            prepare_model.model_validate(
                {
                    "programId": 1001,
                    "ticketCategoryId": 3001,
                    "quantity": 1,
                    "ticketUserIds": [9001],
                    "unitAmountFen": 1,
                }
            )
        with self.assertRaises(ValidationError):
            prepare_model.model_validate(
                {
                    "programId": 1001,
                    "ticketCategoryId": 3001,
                    "quantity": 1,
                    "ticketUserIds": [9001],
                    "confirmationGrant": "forged",
                }
            )
        with self.assertRaises(ValidationError):
            prepare_model.model_validate(
                {
                    "programId": 1001,
                    "ticketCategoryId": 3001,
                    "quantity": 7,
                    "ticketUserIds": list(range(7)),
                }
            )

    def test_recommendation_contract_is_bounded_and_defaults_to_relevance(self) -> None:
        request_model = REQUEST_MODELS["recommend_programs"]
        validated = request_model.model_validate({"keyword": "音乐剧"})
        self.assertEqual(validated.preference, "RELEVANCE")
        self.assertEqual(validated.candidateLimit, 3)
        with self.assertRaises(ValidationError):
            request_model.model_validate({"candidateLimit": 6})

        response = json.loads(
            (CONTRACT_EXAMPLES / "recommendation-response.json").read_text(encoding="utf-8")
        )
        validated_response = RESPONSE_MODELS["recommend_programs"].model_validate(response)
        self.assertEqual(validated_response.data.list[0].rank, 1)
        self.assertEqual(validated_response.data.list[0].totalRemaining, 25)

    def test_watch_contract_enforces_shard_version_and_bounded_mutations(self) -> None:
        create_model = REQUEST_MODELS["create_watch_rule"]
        created = create_model.model_validate({"programId": 1001})
        self.assertEqual(created.minRemaining, 1)
        self.assertEqual(created.checkIntervalSeconds, 300)
        self.assertEqual(created.notificationChannel, "IN_APP")
        with self.assertRaises(ValidationError):
            create_model.model_validate({"programId": 1001, "ticketCategoryIds": list(range(21))})

        update_model = REQUEST_MODELS["update_watch_rule"]
        update = update_model.model_validate(
            {"ruleId": 9, "programId": 1001, "expectedVersion": 2, "maxPrice": 680}
        )
        self.assertEqual(update.expectedVersion, 2)
        with self.assertRaises(ValidationError):
            update_model.model_validate(
                {
                    "ruleId": 9,
                    "programId": 1001,
                    "expectedVersion": 2,
                    "tenantId": "forged",
                }
            )


if __name__ == "__main__":
    unittest.main()
