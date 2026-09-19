from __future__ import annotations

import json
import unittest
from typing import Any, Dict, Sequence

from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    ProviderUsage,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.runner import AgentRunner
from damai_agent.runtime.hooks import (
    AuditRecord,
    BaseRuntimeHook,
    ModelHookContext,
    ToolHookContext,
    ToolOutcome,
    safe_log_label,
)
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class SecretTool(AgentTool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lookup",
            description="lookup",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {"keyword": {"type": "string"}},
                "required": ["keyword"],
            },
        )

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(
            success=True,
            code=0,
            message="ok",
            data={"private": arguments["keyword"]},
        )


class HookProvider:
    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = calls
        self.round = 0
        self.observations: list[ChatMessage] = []

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.round += 1
        if self.round == 1:
            return ProviderResponse(
                tool_calls=self.calls,
                usage=ProviderUsage(prompt_tokens=10, completion_tokens=2),
                finish_reason="tool_calls",
                model_route="test/model",
            )
        self.observations = [message for message in messages if message.role == "tool"]
        return ProviderResponse(
            content="done",
            usage=ProviderUsage(prompt_tokens=8, completion_tokens=3),
            model_route="test/model",
        )


class RecordingHook(BaseRuntimeHook):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def before_model(self, context: ModelHookContext) -> None:
        self.events.append(f"before_model:{context.round_number}")

    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None:
        self.events.append(f"after_model:{context.round_number}")

    async def before_tool(self, context: ToolHookContext) -> ToolResult | None:
        self.events.append(f"before_tool:{context.tool_name}")
        return None

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        self.events.append(f"after_tool:{context.tool_name}")


class BrokenAfterToolHook(BaseRuntimeHook):
    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        raise RuntimeError("do-not-leak-secret")


class BrokenBeforeToolHook(BaseRuntimeHook):
    async def before_tool(self, context: ToolHookContext) -> ToolResult | None:
        raise RuntimeError("do-not-leak-secret")


class RuntimeHooksTest(unittest.IsolatedAsyncioTestCase):
    async def test_hooks_are_per_turn_and_audit_excludes_payloads(self) -> None:
        secret = "secret-private-keyword"
        tool = SecretTool()
        provider = HookProvider([ToolCall("call-1", "lookup", {"keyword": secret})])
        records: list[AuditRecord] = []
        hook_events: list[str] = []

        async def audit_sink(record: AuditRecord) -> None:
            records.append(record)

        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
            audit_sink=audit_sink,
            hook_factories=(lambda: RecordingHook(hook_events),),
        )

        result = await runner.run("lookup", "hook-session")

        self.assertEqual(result.answer, "done")
        self.assertEqual(result.usage.prompt_tokens, 18)
        self.assertEqual(result.usage.completion_tokens, 5)
        self.assertEqual(tool.calls, 1)
        self.assertEqual(
            hook_events,
            [
                "before_model:1",
                "after_model:1",
                "before_tool:lookup",
                "after_tool:lookup",
                "before_model:2",
                "after_model:2",
            ],
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].trace_id, result.trace_id)
        self.assertEqual(records[0].outcome.code, 0)
        self.assertTrue(records[0].occurred_at.endswith("Z"))
        self.assertEqual(records[0].to_dict()["occurredAt"], records[0].occurred_at)
        self.assertNotIn(secret, json.dumps(records[0].to_dict()))

    async def test_usage_is_reset_for_each_turn(self) -> None:
        class SingleResponseProvider:
            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                return ProviderResponse(
                    content="done", usage=ProviderUsage(prompt_tokens=3, completion_tokens=1)
                )

        runner = AgentRunner(
            provider=SingleResponseProvider(),
            registry=ToolRegistry(),
            sessions=InMemorySessionStore(),
        )

        first = await runner.run("first", "first-session")
        second = await runner.run("second", "second-session")

        self.assertEqual(first.usage.total_tokens, 4)
        self.assertEqual(second.usage.total_tokens, 4)

    def test_log_labels_reject_untrusted_control_characters(self) -> None:
        self.assertEqual(safe_log_label("test/model"), "test/model")
        self.assertEqual(safe_log_label("secret\nforged log line"), "<redacted>")

    async def test_unavailable_tool_is_denied_and_audited_without_untrusted_name(self) -> None:
        secret_name = "fake-tool\nsecret-name"
        provider = HookProvider([ToolCall("bad\nsecret-id", secret_name, {})])
        records: list[AuditRecord] = []
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(),
            sessions=InMemorySessionStore(),
            audit_sink=lambda record: records.append(record),
        )

        await runner.run("lookup", "hook-denial-session")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].tool_name, "<unavailable>")
        self.assertTrue(records[0].tool_call_id.startswith("sha256:"))
        self.assertEqual(records[0].outcome.error_code.value, "TOOL_SCOPE_DENIED")
        self.assertNotIn(secret_name, json.dumps(records[0].to_dict()))
        self.assertNotIn("secret-id", json.dumps(records[0].to_dict()))

    async def test_broken_after_hook_cannot_erase_matching_tool_result(self) -> None:
        provider = HookProvider([ToolCall("call-1", "lookup", {"keyword": "ok"})])
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([SecretTool()]),
            sessions=InMemorySessionStore(),
            hook_factories=(BrokenAfterToolHook,),
        )

        result = await runner.run("lookup", "hook-broken-after-session")

        self.assertEqual(result.answer, "done")
        self.assertEqual(len(provider.observations), 1)
        self.assertTrue(json.loads(provider.observations[0].content or "{}")["success"])

    async def test_audit_sink_failure_does_not_erase_matching_tool_result(self) -> None:
        provider = HookProvider([ToolCall("call-1", "lookup", {"keyword": "ok"})])

        def broken_sink(record: AuditRecord) -> None:
            raise RuntimeError("do-not-leak-secret")

        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([SecretTool()]),
            sessions=InMemorySessionStore(),
            audit_sink=broken_sink,
        )

        result = await runner.run("lookup", "hook-broken-audit-session")

        self.assertEqual(result.answer, "done")
        self.assertTrue(json.loads(provider.observations[0].content or "{}")["success"])

    async def test_strict_audit_failure_stops_before_next_model_call(self) -> None:
        provider = HookProvider([ToolCall("call-1", "lookup", {"keyword": "ok"})])
        tool = SecretTool()

        def broken_sink(record: AuditRecord) -> None:
            raise RuntimeError("private-database-error")

        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
            audit_sink=broken_sink,
            strict_audit=True,
        )
        with self.assertRaisesRegex(RuntimeError, "tool audit persistence failed"):
            await runner.run("lookup", "strict-audit-session")
        self.assertEqual(tool.calls, 1)
        self.assertEqual(provider.round, 1)

    async def test_broken_before_hook_fails_closed_without_invoking_tool(self) -> None:
        tool = SecretTool()
        provider = HookProvider([ToolCall("call-1", "lookup", {"keyword": "ok"})])
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry([tool]),
            sessions=InMemorySessionStore(),
            hook_factories=(BrokenBeforeToolHook,),
        )

        await runner.run("lookup", "hook-broken-before-session")

        self.assertEqual(tool.calls, 0)
        result = json.loads(provider.observations[0].content or "{}")
        self.assertEqual(result["errorCode"], "TOOL_EXECUTION_FAILED")
        self.assertNotIn("do-not-leak-secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
