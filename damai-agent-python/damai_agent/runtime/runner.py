"""Pure model-to-tool execution loop without transport or session persistence."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import List, Optional, Protocol, Sequence, cast

from ..config import ModelPrice
from ..governance import TenantQuota, TurnBudget
from ..models import (
    AgentErrorCode,
    AgentEvent,
    AgentRunResult,
    AgentRunSpec,
    ChatMessage,
    KnowledgeCitation,
    ProviderResponse,
    ProviderStreamEventType,
    ProviderUsage,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from ..observability import RuntimeMetrics
from ..providers import ModelProvider, ProviderStreamAccumulator
from ..rag import RagBundle, StableKnowledgeRag, requires_live_tool
from ..recommendation import RecommendationConstraintGuard
from ..tools import ToolCallLedger, ToolRegistry
from ..tracing import TraceManager
from .context import ContextGovernor, valid_tool_calls
from .events import TurnEventEmitter
from .hooks import (
    AuditSink,
    HookChain,
    HookFactory,
    ModelHookContext,
    ToolHookContext,
    ToolOutcome,
    elapsed_ms,
    safe_log_label,
    safe_tool_call_id,
)

REJECTED_FINISH_REASONS = frozenset({"content_filter", "error", "refusal"})


class ToolRoundRecorder(Protocol):
    """Fail-closed persistence boundary for an opt-in durable Turn."""

    async def ensure_active(self) -> None: ...

    async def before_tool_round(
        self, spec: AgentRunSpec, iteration: int, assistant: ChatMessage, model_route: str
    ) -> None: ...

    async def record_tool_result(self, tool_call_id: str, result: ToolResult) -> None: ...

    async def commit_tool_round(self, messages: Sequence[ChatMessage]) -> None: ...


class ToolCallingRunner:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        tool_timeout_seconds: float = 8.0,
        max_concurrent_read_tools: int = 4,
        audit_sink: Optional[AuditSink] = None,
        hook_factories: Sequence[HookFactory] = (),
        max_context_chars: int = 80000,
        max_tool_result_chars: int = 16000,
        max_turn_tokens: int = 0,
        max_turn_cost_micro_usd: int = 0,
        pricing_catalog: dict[str, ModelPrice] | None = None,
        metrics: RuntimeMetrics | None = None,
        tracing: TraceManager | None = None,
        tenant_quota: TenantQuota | None = None,
        tenant_daily_cost_micro_usd: int = 0,
        strict_audit: bool = False,
        knowledge_rag: StableKnowledgeRag | None = None,
    ) -> None:
        if not 1 <= max_concurrent_read_tools <= 12:
            raise ValueError("max_concurrent_read_tools must be between 1 and 12")
        self._provider = provider
        self._registry = registry
        self._tool_timeout_seconds = tool_timeout_seconds
        self._max_concurrent_read_tools = max_concurrent_read_tools
        self._audit_sink = audit_sink
        self._hook_factories = tuple(hook_factories)
        self._context_governor = ContextGovernor(max_context_chars, max_tool_result_chars)
        self._max_turn_tokens = max_turn_tokens
        self._max_turn_cost_micro_usd = max_turn_cost_micro_usd
        self._pricing_catalog = pricing_catalog or {}
        self._metrics = metrics
        self._tracing = tracing or TraceManager()
        if tenant_daily_cost_micro_usd and tenant_quota is None:
            raise ValueError("tenant quota store is required")
        self._tenant_quota = tenant_quota
        self._tenant_daily_cost_micro_usd = tenant_daily_cost_micro_usd
        self._strict_audit = strict_audit
        self._knowledge_rag = knowledge_rag

    @property
    def tool_names(self) -> List[str]:
        return self._registry.names

    @property
    def registered_specs(self) -> List[ToolSpec]:
        return self._registry.specs

    @property
    def model_route(self) -> str:
        return str(getattr(self._provider, "route_name", type(self._provider).__name__))

    def available_specs(self, spec: AgentRunSpec) -> List[ToolSpec]:
        authorized_specs = {
            item.name: item for item in self._registry.available_specs(spec.context)
        }
        # RunSpec limits visibility; the registry remains authoritative for risk and scheduling.
        return [
            authorized_specs[item.name] for item in spec.tool_specs if item.name in authorized_specs
        ]

    async def run(
        self,
        spec: AgentRunSpec,
        emitter: TurnEventEmitter,
        *,
        recorder: ToolRoundRecorder | None = None,
    ) -> AgentRunResult:
        started_at = time.perf_counter()
        try:
            with self._tracing.span(
                "agent.turn",
                trace_id=spec.context.trace_id,
                attributes={"agent.risk_ceiling": spec.context.risk_ceiling.value},
            ) as turn_span:
                result = await self._run_impl(spec, emitter, recorder=recorder)
                if result.error_code is not None:
                    self._tracing.mark_error(turn_span)
        except BaseException:
            if self._metrics is not None:
                self._metrics.observe_turn("error", elapsed_ms(started_at))
            raise
        if self._metrics is not None:
            rejected_codes = {
                AgentErrorCode.CONTEXT_BUDGET_EXCEEDED,
                AgentErrorCode.CONTEXT_PROTOCOL_INVALID,
                AgentErrorCode.TURN_BUDGET_EXCEEDED,
                AgentErrorCode.TENANT_QUOTA_EXCEEDED,
            }
            self._metrics.observe_turn(
                (
                    "success"
                    if result.error_code is None
                    else "rejected"
                    if result.error_code in rejected_codes
                    else "error"
                ),
                elapsed_ms(started_at),
            )
        return result

    async def _run_impl(
        self,
        spec: AgentRunSpec,
        emitter: TurnEventEmitter,
        *,
        recorder: ToolRoundRecorder | None = None,
    ) -> AgentRunResult:
        if not spec.messages or spec.messages[-1].role != "user":
            raise ValueError("AgentRunSpec.messages 必须以当前 user 消息结束")

        available_specs = self.available_specs(spec)
        allowed_tool_names = {tool_spec.name for tool_spec in available_specs}
        risk_by_name = {item.name: item.risk for item in available_specs}
        hooks = HookChain(
            frozenset(allowed_tool_names),
            audit_sink=self._audit_sink,
            extra_factories=self._hook_factories,
            strict_audit=self._strict_audit,
        )
        budget = TurnBudget(
            self._max_turn_tokens, self._max_turn_cost_micro_usd, self._pricing_catalog
        )
        messages = [ChatMessage(role="system", content=spec.system_prompt), *spec.messages]
        turn_messages: List[ChatMessage] = [spec.messages[-1]]
        tools_used: List[str] = []
        tool_events: List[AgentEvent] = []
        model_route = spec.model_route
        ledger = ToolCallLedger(max_calls=spec.max_tool_calls)
        dynamic_fact_query = requires_live_tool(spec.messages[-1].content or "")
        recommendation_guard = RecommendationConstraintGuard.from_user_text(
            spec.messages[-1].content or ""
        )
        rag_bundle = RagBundle()
        if self._knowledge_rag is not None:
            try:
                with self._tracing.span("agent.knowledge") as knowledge_span:
                    rag_bundle = await self._knowledge_rag.prepare(
                        spec.messages[-1].content or "",
                        tenant_id=spec.context.tenant_id,
                        locale=spec.context.locale,
                    )
                    knowledge_span.set_attribute("agent.knowledge_outcome", rag_bundle.outcome)
                    knowledge_span.set_attribute("agent.knowledge_hits", len(rag_bundle.citations))
            except BaseException:
                if self._metrics is not None:
                    self._metrics.observe_knowledge("error")
                raise
            if self._metrics is not None:
                self._metrics.observe_knowledge(rag_bundle.outcome)
            await emitter.emit(
                "knowledge.retrieved",
                {
                    "outcome": rag_bundle.outcome,
                    "citationCount": len(rag_bundle.citations),
                    "knowledgeVersion": rag_bundle.index_version,
                },
            )
        if rag_bundle.context:
            messages[0] = ChatMessage(
                role="system", content=f"{spec.system_prompt}{rag_bundle.context}"
            )

        for round_number in range(1, spec.max_tool_rounds + 1):
            if recorder is not None:
                await recorder.ensure_active()
            try:
                prepared = self._context_governor.prepare(messages, available_specs)
            except ValueError:
                return self._governor_failure(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    AgentErrorCode.CONTEXT_PROTOCOL_INVALID,
                    budget.cost_micro_usd,
                )
            if prepared is None:
                return self._governor_failure(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    AgentErrorCode.CONTEXT_BUDGET_EXCEEDED,
                    budget.cost_micro_usd,
                )
            messages = prepared
            if self._tenant_quota is not None and await self._tenant_quota.exhausted(
                spec.context.tenant_id, self._tenant_daily_cost_micro_usd
            ):
                return self._governor_failure(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    AgentErrorCode.TENANT_QUOTA_EXCEEDED,
                    budget.cost_micro_usd,
                )
            model_context = ModelHookContext(
                turn_id=spec.context.turn_id,
                trace_id=spec.context.trace_id,
                round_number=round_number,
                model_route=model_route,
            )
            await hooks.before_model(model_context)
            model_started_at = time.perf_counter()
            try:
                with self._tracing.span(
                    "agent.model",
                    attributes={"agent.model_route": safe_log_label(model_route)},
                ):
                    response = await self._invoke_provider(
                        messages,
                        available_specs,
                        emitter,
                        round_number,
                        buffer_text=rag_bundle.citation_required or dynamic_fact_query,
                        buffer_tool_calls=recommendation_guard.active,
                    )
            except BaseException:
                if self._metrics is not None:
                    self._metrics.observe_model(False, elapsed_ms(model_started_at))
                raise
            hardened_calls, hardened_count = recommendation_guard.apply(response.tool_calls)
            if hardened_count:
                response = replace(response, tool_calls=list(hardened_calls))
                await emitter.emit(
                    "recommendation.constraints.applied",
                    {"types": ["maxPrice"], "toolCalls": hardened_count},
                )
            model_route = response.model_route or model_route
            model_duration_ms = elapsed_ms(model_started_at)
            await hooks.after_model(
                ModelHookContext(
                    turn_id=spec.context.turn_id,
                    trace_id=spec.context.trace_id,
                    round_number=round_number,
                    model_route=model_route,
                ),
                response.usage,
                response.finish_reason,
                model_duration_ms,
            )
            previous_cost = budget.cost_micro_usd
            budget_error = budget.record(model_route, response.usage)
            current_cost = budget.cost_micro_usd
            incremental_cost = (
                current_cost - previous_cost
                if current_cost is not None and previous_cost is not None
                else None
            )
            if self._tenant_quota is not None:
                if incremental_cost is None:
                    budget_error = AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE
                elif not await self._tenant_quota.charge(
                    spec.context.tenant_id,
                    spec.context.turn_id,
                    round_number,
                    incremental_cost,
                    self._tenant_daily_cost_micro_usd,
                ):
                    budget_error = AgentErrorCode.TENANT_QUOTA_EXCEEDED
            if self._metrics is not None:
                self._metrics.observe_model(
                    True,
                    model_duration_ms,
                    response.usage,
                    incremental_cost,
                )
            await emitter.emit(
                "model.completed",
                {
                    "round": round_number,
                    "finishReason": response.finish_reason,
                    "toolCalls": [call.name for call in response.tool_calls],
                    "usage": response.usage.to_dict(),
                    "modelRoute": model_route,
                    "costMicroUsd": budget.cost_micro_usd,
                },
            )

            if budget_error is not None:
                return self._governor_failure(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    budget_error,
                    budget.cost_micro_usd,
                )

            if response.refusal or response.finish_reason in REJECTED_FINISH_REASONS:
                return self._rejected_result(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    response.finish_reason,
                    budget.cost_micro_usd,
                )

            if not valid_tool_calls(response.tool_calls):
                return self._governor_failure(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    hooks.usage.total,
                    model_route,
                    AgentErrorCode.CONTEXT_PROTOCOL_INVALID,
                    budget.cost_micro_usd,
                )

            assistant = self._assistant_message(response)
            if recorder is not None and response.tool_calls:
                await recorder.before_tool_round(spec, round_number, assistant, model_route)

            if not response.tool_calls:
                answer = (response.content or "暂时无法生成回答，请稍后重试。").strip()
                if dynamic_fact_query and not tools_used:
                    return self._dynamic_fact_failure(
                        spec,
                        turn_messages,
                        tool_events,
                        hooks.usage.total,
                        model_route,
                        budget.cost_micro_usd,
                        rag_bundle.index_version,
                    )
                citations: tuple[KnowledgeCitation, ...] = ()
                if rag_bundle.citation_required:
                    selected = rag_bundle.cited_by(answer)
                    if selected is None:
                        return self._knowledge_failure(
                            spec,
                            turn_messages,
                            tools_used,
                            tool_events,
                            hooks.usage.total,
                            model_route,
                            budget.cost_micro_usd,
                            rag_bundle.index_version,
                        )
                    citations = selected
                if rag_bundle.citation_required or dynamic_fact_query:
                    await emitter.emit("model.text.delta", {"round": round_number, "delta": answer})
                messages.append(assistant)
                turn_messages.append(assistant)
                return AgentRunResult(
                    session_key=spec.context.session_key,
                    turn_id=spec.context.turn_id,
                    trace_id=spec.context.trace_id,
                    final_content=answer,
                    tools_used=tuple(tools_used),
                    messages=tuple(turn_messages),
                    usage=hooks.usage.total,
                    cost_micro_usd=budget.cost_micro_usd,
                    stop_reason=response.finish_reason,
                    tool_events=tuple(tool_events),
                    model_route=model_route,
                    citations=citations,
                    knowledge_version=rag_bundle.index_version,
                )

            messages.append(assistant)
            turn_messages.append(assistant)

            parallel_names = {
                item.name
                for item in available_specs
                if item.risk is ToolRisk.READ_ONLY and item.concurrency_safe and not item.exclusive
            }
            for batch in self._tool_batches(response.tool_calls, parallel_names):
                if recorder is not None:
                    await recorder.ensure_active()
                for call in batch:
                    tools_used.append(call.name)
                    started = await emitter.emit(
                        "tool.started", {"toolCallId": call.id, "tool": call.name}
                    )
                    tool_events.append(started)

                # Keep provider call order even if individual read-only tools finish out of order.
                if recorder is None:
                    results = await asyncio.gather(
                        *(
                            self._execute_call(call, spec, risk_by_name, ledger, hooks)
                            for call in batch
                        )
                    )
                else:
                    outcomes = await asyncio.gather(
                        *(
                            self._execute_and_record(
                                call, spec, risk_by_name, ledger, hooks, recorder
                            )
                            for call in batch
                        ),
                        return_exceptions=True,
                    )
                    for outcome in outcomes:
                        if isinstance(outcome, BaseException):
                            raise outcome
                    results = [cast(ToolResult, outcome) for outcome in outcomes]
                for call, tool_result in zip(batch, results, strict=True):
                    tool_message = ChatMessage(
                        role="tool",
                        content=tool_result.to_model_content(),
                        name=call.name,
                        tool_call_id=call.id,
                    )
                    messages.append(tool_message)
                    turn_messages.append(tool_message)
                    completed = await emitter.emit(
                        "tool.completed",
                        {
                            "toolCallId": call.id,
                            "tool": call.name,
                            "success": tool_result.success,
                            "code": tool_result.code,
                            "errorCode": (
                                tool_result.error_code.value if tool_result.error_code else None
                            ),
                            "retryable": tool_result.retryable,
                        },
                    )
                    tool_events.append(completed)
            if recorder is not None:
                await recorder.commit_tool_round(tuple(turn_messages))

        answer = "本轮调用工具次数已达到上限，请缩小查询范围后重试。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=hooks.usage.total,
            cost_micro_usd=budget.cost_micro_usd,
            stop_reason="max_tool_rounds",
            tool_events=tuple(tool_events),
            model_route=model_route,
        )

    def _tool_batches(
        self, calls: Sequence[ToolCall], parallel_names: set[str]
    ) -> List[List[ToolCall]]:
        batches: List[List[ToolCall]] = []
        parallel_batch: List[ToolCall] = []
        for call in calls:
            if call.name not in parallel_names:
                if parallel_batch:
                    batches.append(parallel_batch)
                    parallel_batch = []
                batches.append([call])
                continue
            parallel_batch.append(call)
            if len(parallel_batch) == self._max_concurrent_read_tools:
                batches.append(parallel_batch)
                parallel_batch = []
        if parallel_batch:
            batches.append(parallel_batch)
        return batches

    async def _execute_call(
        self,
        call: ToolCall,
        spec: AgentRunSpec,
        risk_by_name: dict[str, ToolRisk],
        ledger: ToolCallLedger,
        hooks: HookChain,
    ) -> ToolResult:
        context = ToolHookContext(
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            tool_call_id=safe_tool_call_id(call.id),
            tool_name=call.name if call.name in risk_by_name else "<unavailable>",
            risk=risk_by_name.get(call.name),
            tenant_id=spec.context.tenant_id,
            session_key=spec.context.session_key,
        )
        started_at = time.perf_counter()
        try:
            with self._tracing.span(
                "agent.tool",
                attributes={"agent.tool_name": safe_log_label(context.tool_name)},
            ) as tool_span:
                decision = await hooks.before_tool(context)
                result = decision or await self._registry.execute(
                    call.name,
                    call.arguments,
                    ToolContext.from_turn(spec.context, call.id),
                    timeout_seconds=self._tool_timeout_seconds,
                    ledger=ledger,
                )
                result = self._context_governor.bound_tool_result(result)
                if not result.success:
                    self._tracing.mark_error(tool_span)
                await hooks.after_tool(
                    context, ToolOutcome.from_result(result, elapsed_ms(started_at))
                )
        except BaseException:
            if self._metrics is not None:
                self._metrics.observe_tool(False, elapsed_ms(started_at))
            raise
        if self._metrics is not None:
            self._metrics.observe_tool(result.success, elapsed_ms(started_at))
        return result

    async def _execute_and_record(
        self,
        call: ToolCall,
        spec: AgentRunSpec,
        risk_by_name: dict[str, ToolRisk],
        ledger: ToolCallLedger,
        hooks: HookChain,
        recorder: ToolRoundRecorder,
    ) -> ToolResult:
        await recorder.ensure_active()
        result = await self._execute_call(call, spec, risk_by_name, ledger, hooks)
        await recorder.record_tool_result(call.id, result)
        return result

    def _governor_failure(
        self,
        spec: AgentRunSpec,
        turn_messages: List[ChatMessage],
        tools_used: List[str],
        tool_events: List[AgentEvent],
        usage: ProviderUsage,
        model_route: str,
        code: AgentErrorCode,
        cost_micro_usd: int | None,
    ) -> AgentRunResult:
        if code is AgentErrorCode.CONTEXT_BUDGET_EXCEEDED:
            answer = "对话上下文过长，请缩短问题或开启新会话。"
        elif code is AgentErrorCode.TURN_BUDGET_EXCEEDED:
            answer = "本轮模型使用量已达预算上限，已停止后续工具调用。"
        elif code is AgentErrorCode.MODEL_ACCOUNTING_UNAVAILABLE:
            answer = "模型用量或定价信息不可核实，已停止后续工具调用。"
        elif code is AgentErrorCode.TENANT_QUOTA_EXCEEDED:
            answer = "今日租户模型额度已用尽，已停止后续工具调用。"
        else:
            answer = "工具调用协议不完整，本轮已安全停止。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=usage,
            cost_micro_usd=cost_micro_usd,
            stop_reason=code.value.lower(),
            error_code=code,
            tool_events=tuple(tool_events),
            model_route=model_route,
        )

    async def _invoke_provider(
        self,
        messages: List[ChatMessage],
        available_specs: List[ToolSpec],
        emitter: TurnEventEmitter,
        round_number: int,
        *,
        buffer_text: bool = False,
        buffer_tool_calls: bool = False,
    ) -> ProviderResponse:
        stream = getattr(self._provider, "stream", None)
        if not callable(stream):
            return await self._provider.complete(messages, available_specs)

        accumulator = ProviderStreamAccumulator()
        async for event in stream(messages, available_specs):
            accumulator.add(event)
            if (
                event.event_type is ProviderStreamEventType.TEXT_DELTA
                and event.text_delta
                and not buffer_text
            ):
                await emitter.emit(
                    "model.text.delta",
                    {"round": round_number, "delta": event.text_delta},
                )
            elif (
                event.event_type is ProviderStreamEventType.TOOL_CALL_DELTA
                and not buffer_tool_calls
            ):
                await emitter.emit(
                    "model.tool_call.delta",
                    {
                        "round": round_number,
                        "index": event.tool_call_index,
                        "toolCallId": event.tool_call_id,
                        "tool": event.tool_name,
                        "argumentsDelta": event.arguments_delta,
                    },
                )
            elif event.event_type is ProviderStreamEventType.USAGE:
                await emitter.emit(
                    "model.usage",
                    {"round": round_number, "usage": event.usage.to_dict()},
                )
        return accumulator.build()

    def _assistant_message(self, response: ProviderResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )

    def _knowledge_failure(
        self,
        spec: AgentRunSpec,
        turn_messages: List[ChatMessage],
        tools_used: List[str],
        tool_events: List[AgentEvent],
        usage: ProviderUsage,
        model_route: str,
        cost_micro_usd: int | None,
        knowledge_version: str,
    ) -> AgentRunResult:
        answer = "知识回答缺少可验证来源，已安全停止，请稍后重试。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=usage,
            cost_micro_usd=cost_micro_usd,
            stop_reason="knowledge_citation_invalid",
            error_code=AgentErrorCode.KNOWLEDGE_CITATION_INVALID,
            tool_events=tuple(tool_events),
            model_route=model_route,
            knowledge_version=knowledge_version,
        )

    def _dynamic_fact_failure(
        self,
        spec: AgentRunSpec,
        turn_messages: List[ChatMessage],
        tool_events: List[AgentEvent],
        usage: ProviderUsage,
        model_route: str,
        cost_micro_usd: int | None,
        knowledge_version: str,
    ) -> AgentRunResult:
        answer = "动态票务事实尚未经过实时业务服务核实，已安全停止。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=(),
            messages=tuple(turn_messages),
            usage=usage,
            cost_micro_usd=cost_micro_usd,
            stop_reason="dynamic_fact_tool_required",
            error_code=AgentErrorCode.DYNAMIC_FACT_TOOL_REQUIRED,
            tool_events=tuple(tool_events),
            model_route=model_route,
            knowledge_version=knowledge_version,
        )

    def _rejected_result(
        self,
        spec: AgentRunSpec,
        turn_messages: List[ChatMessage],
        tools_used: List[str],
        tool_events: List[AgentEvent],
        usage: ProviderUsage,
        model_route: str,
        finish_reason: str,
        cost_micro_usd: int | None,
    ) -> AgentRunResult:
        answer = "模型未能安全完成本轮请求，请调整问题后重试。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=usage,
            cost_micro_usd=cost_micro_usd,
            stop_reason=finish_reason or "rejected",
            error_code=AgentErrorCode.PROVIDER_FINISH_REJECTED,
            tool_events=tuple(tool_events),
            model_route=model_route,
        )
