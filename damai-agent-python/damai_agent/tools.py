"""Tool registry and Java Tool Gateway adapters."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from jsonschema import Draft7Validator, FormatChecker
from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from .generated.tool_models import REQUEST_MODELS, RESPONSE_MODELS
from .generated.tool_schemas import TOOL_DEFINITIONS
from .models import (
    TOOL_RISK_ORDER,
    AgentErrorCode,
    TicketTurnContext,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)


class AgentTool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError


@dataclass(slots=True)
class ToolCallLedger:
    max_calls: int
    total_calls: int = 0
    calls_by_tool: Dict[str, int] = field(default_factory=dict)

    def reserve(self, spec: ToolSpec) -> bool:
        current = self.calls_by_tool.get(spec.name, 0)
        if self.total_calls >= self.max_calls or current >= spec.max_calls_per_turn:
            return False
        self.total_calls += 1
        self.calls_by_tool[spec.name] = current + 1
        return True


class ToolRegistry:
    def __init__(self, tools: Iterable[AgentTool] = ()) -> None:
        self._tools: Dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"工具已注册: {name}")
        self._tools[name] = tool

    @property
    def specs(self) -> List[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    @property
    def names(self) -> List[str]:
        return list(self._tools)

    def available_specs(self, context: TicketTurnContext) -> List[ToolSpec]:
        return [
            tool.spec
            for tool in self._tools.values()
            if self._is_authorized(tool.spec, context.tool_scopes, context.risk_ceiling)
        ]

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: ToolContext,
        timeout_seconds: float,
        ledger: ToolCallLedger | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                code=404,
                message=f"未知工具: {name}",
                retryable=False,
                error_code=AgentErrorCode.TOOL_NOT_FOUND,
            )

        try:
            normalized_arguments = self._normalize_arguments(
                tool.spec.parameters,
                arguments,
                tool.spec.request_model,
            )
        except ValueError as error:
            return ToolResult(
                success=False,
                code=400,
                message=str(error),
                retryable=False,
                error_code=AgentErrorCode.TOOL_ARGUMENT_INVALID,
            )

        if tool.spec.required_scope and tool.spec.required_scope not in context.tool_scopes:
            return ToolResult(
                success=False,
                code=403,
                message=f"缺少工具权限: {tool.spec.required_scope}",
                retryable=False,
                error_code=AgentErrorCode.TOOL_SCOPE_DENIED,
            )
        if not self._risk_allowed(tool.spec.risk, context.risk_ceiling):
            return ToolResult(
                success=False,
                code=403,
                message=f"工具风险等级不允许: {tool.spec.risk.value}",
                retryable=False,
                error_code=AgentErrorCode.TOOL_RISK_DENIED,
            )
        if ledger is not None and not ledger.reserve(tool.spec):
            return ToolResult(
                success=False,
                code=429,
                message=f"工具 {name} 已达到本轮调用上限",
                retryable=False,
                error_code=AgentErrorCode.TOOL_CALL_LIMIT_EXCEEDED,
            )

        effective_timeout = min(timeout_seconds, tool.spec.timeout_ms / 1000)
        try:
            return await asyncio.wait_for(
                tool.execute(normalized_arguments, context), timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                code=504,
                message=f"工具 {name} 执行超时",
                retryable=True,
                error_code=AgentErrorCode.TOOL_TIMEOUT,
            )
        except Exception as error:  # Registry is the isolation boundary for tools.
            return ToolResult(
                success=False,
                code=500,
                message=f"工具 {name} 执行异常: {type(error).__name__}",
                retryable=True,
                error_code=AgentErrorCode.TOOL_EXECUTION_FAILED,
            )

    def _is_authorized(
        self,
        spec: ToolSpec,
        tool_scopes: frozenset[str],
        risk_ceiling: ToolRisk,
    ) -> bool:
        has_scope = not spec.required_scope or spec.required_scope in tool_scopes
        return has_scope and self._risk_allowed(spec.risk, risk_ceiling)

    def _risk_allowed(self, risk: ToolRisk, ceiling: ToolRisk) -> bool:
        return risk is not ToolRisk.PROHIBITED and TOOL_RISK_ORDER[risk] <= TOOL_RISK_ORDER[ceiling]

    def _normalize_arguments(
        self,
        schema: Dict[str, Any],
        arguments: Dict[str, Any],
        request_model: type[BaseModel] | None = None,
    ) -> Dict[str, Any]:
        normalized = self._coerce_value(schema, copy.deepcopy(arguments))
        if not isinstance(normalized, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        if request_model is not None:
            try:
                validated = request_model.model_validate(normalized)
            except ValidationError as error:
                first_error = error.errors(include_input=False)[0]
                field_name = ".".join(str(item) for item in first_error["loc"]) or "$"
                raise ValueError(
                    f"工具参数校验失败: {field_name} 不符合 {first_error['type']} 约束"
                ) from error
            return validated.model_dump(mode="json", by_alias=True, exclude_none=True)
        validator = Draft7Validator(schema, format_checker=FormatChecker())
        validation_errors = sorted(
            validator.iter_errors(normalized),
            key=lambda item: ".".join(str(part) for part in item.absolute_path),
        )
        if validation_errors:
            validation_error = validation_errors[0]
            field_name = ".".join(str(item) for item in validation_error.absolute_path) or "$"
            raise ValueError(
                f"工具参数校验失败: {field_name} 不符合 {validation_error.validator} 约束"
            )
        return normalized

    def _coerce_value(self, schema: Dict[str, Any], value: Any) -> Any:
        schema_type = schema.get("type")
        if schema_type == "object" and isinstance(value, dict):
            properties = schema.get("properties", {})
            normalized: Dict[str, Any] = {}
            for key, item in value.items():
                property_schema = properties.get(key)
                normalized[key] = (
                    self._coerce_value(property_schema, item)
                    if isinstance(property_schema, dict)
                    else item
                )
            for key, property_schema in properties.items():
                if key not in normalized and isinstance(property_schema, dict):
                    if "default" in property_schema:
                        normalized[key] = copy.deepcopy(property_schema["default"])
            return normalized
        if schema_type == "array" and isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                return [self._coerce_value(item_schema, item) for item in value]
            return value
        if schema_type == "integer" and isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"-?(?:0|[1-9]\d*)", stripped):
                return int(stripped)
        if schema_type == "number" and isinstance(value, str):
            stripped = value.strip()
            try:
                number = float(stripped)
            except ValueError:
                return value
            return number if math.isfinite(number) else value
        if schema_type == "boolean" and isinstance(value, str):
            normalized_boolean = value.strip().lower()
            if normalized_boolean in {"true", "false"}:
                return normalized_boolean == "true"
        return value


class JavaToolClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def post(
        self,
        path: str,
        payload: Dict[str, Any],
        context: ToolContext,
        response_model: type[BaseModel] | None = None,
    ) -> ToolResult:
        return await asyncio.to_thread(
            self._post_sync,
            path,
            payload,
            context,
            response_model,
        )

    def _post_sync(
        self,
        path: str,
        payload: Dict[str, Any],
        context: ToolContext,
        response_model: type[BaseModel] | None,
    ) -> ToolResult:
        active_span = trace.get_current_span().get_span_context()
        trace_id = f"{active_span.trace_id:032x}" if active_span.is_valid else context.trace_id
        traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Key": self._api_key,
                "X-Agent-Session-Key": context.session_key,
                "X-Agent-Turn-Id": context.turn_id,
                "X-Agent-Tool-Call-Id": context.tool_call_id,
                "traceparent": traceparent,
            },
            method="POST",
        )
        status = 200
        response_traceparent = None
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                status = response.status
                response_traceparent = response.headers.get("traceparent")
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            response_traceparent = error.headers.get("traceparent")
            body = error.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError):
            return ToolResult(
                success=False,
                code=503,
                message="Java 业务服务不可用",
                retryable=True,
            )

        if status < 400 and response_traceparent != traceparent:
            return ToolResult(
                success=False,
                code=502,
                message="Java 业务服务未正确回传 Trace 上下文",
                retryable=True,
            )

        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            return ToolResult(
                success=False,
                code=status,
                message="Java 业务服务返回了非 JSON 数据",
                retryable=status >= 500,
            )

        if not isinstance(decoded, dict):
            return ToolResult(
                success=False,
                code=502,
                message="Java 业务服务响应结构不符合 Tool 契约",
                retryable=True,
                error_code=AgentErrorCode.TOOL_RESULT_INVALID,
            )

        if status < 400 and response_model is not None:
            try:
                validated_response = response_model.model_validate(decoded)
            except ValidationError:
                return ToolResult(
                    success=False,
                    code=502,
                    message="Java 业务服务响应结构不符合 Tool 契约",
                    retryable=True,
                    error_code=AgentErrorCode.TOOL_RESULT_INVALID,
                )
            decoded = validated_response.model_dump(mode="json", by_alias=True)
            if decoded.get("requestId") != context.tool_call_id:
                return ToolResult(
                    success=False,
                    code=502,
                    message="Java 业务服务响应关联标识不匹配",
                    retryable=True,
                    error_code=AgentErrorCode.TOOL_RESULT_INVALID,
                )

        if "success" in decoded:
            return ToolResult(
                success=bool(decoded.get("success")),
                code=int(decoded.get("code") or 0),
                message=str(decoded.get("message") or ""),
                data=decoded.get("data"),
                retryable=bool(decoded.get("retryable")),
                freshness_at=decoded.get("freshnessAt"),
            )

        # Compatibility with the project's existing ApiResponse exception body.
        code = int(decoded.get("code") if decoded.get("code") is not None else status)
        return ToolResult(
            success=code == 0,
            code=code,
            message=str(decoded.get("message") or ""),
            data=decoded.get("data"),
            retryable=status >= 500,
        )


class JavaReadTool(AgentTool):
    def __init__(self, client: JavaToolClient, spec: ToolSpec, path: str) -> None:
        self._client = client
        self._spec = spec
        self._path = path

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        return await self._client.post(
            self._path,
            arguments,
            context,
            self._spec.response_model,
        )


def build_java_tools(client: JavaToolClient) -> List[AgentTool]:
    tools: List[AgentTool] = []
    for definition in TOOL_DEFINITIONS:
        tools.append(
            JavaReadTool(
                client,
                ToolSpec(
                    name=str(definition["name"]),
                    version=str(definition["version"]),
                    description=str(definition["description"]),
                    parameters=copy.deepcopy(definition["parameters"]),
                    risk=ToolRisk(str(definition["risk"])),
                    required_scope=str(definition["required_scope"]),
                    timeout_ms=int(definition["timeout_ms"]),
                    max_calls_per_turn=int(definition["max_calls_per_turn"]),
                    concurrency_safe=bool(definition["concurrency_safe"]),
                    exclusive=bool(definition["exclusive"]),
                    request_model=REQUEST_MODELS[str(definition["name"])],
                    response_model=RESPONSE_MODELS[str(definition["name"])],
                ),
                str(definition["path"]),
            )
        )
    return tools
