"""Tool registry and Java Tool Gateway adapters."""

from __future__ import annotations

import asyncio
import copy
import json
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List

from .generated.tool_schemas import TOOL_DEFINITIONS
from .models import ToolContext, ToolResult, ToolSpec


class AgentTool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError


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

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        context: ToolContext,
        timeout_seconds: float,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                code=404,
                message=f"未知工具: {name}",
                retryable=False,
            )
        try:
            return await asyncio.wait_for(tool.execute(arguments, context), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                code=504,
                message=f"工具 {name} 执行超时",
                retryable=True,
            )
        except Exception as error:  # Registry is the isolation boundary for tools.
            return ToolResult(
                success=False,
                code=500,
                message=f"工具 {name} 执行异常: {type(error).__name__}",
                retryable=True,
            )


class JavaToolClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def post(self, path: str, payload: Dict[str, Any], context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(self._post_sync, path, payload, context)

    def _post_sync(self, path: str, payload: Dict[str, Any], context: ToolContext) -> ToolResult:
        traceparent = f"00-{context.trace_id}-{uuid.uuid4().hex[:16]}-01"
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
        except (urllib.error.URLError, TimeoutError) as error:
            detail = error.reason if hasattr(error, "reason") else error
            return ToolResult(
                success=False,
                code=503,
                message=f"Java 业务服务不可用: {detail}",
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
        return await self._client.post(self._path, arguments, context)


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
                    risk=str(definition["risk"]),
                    required_scope=str(definition["required_scope"]),
                    timeout_ms=int(definition["timeout_ms"]),
                ),
                str(definition["path"]),
            )
        )
    return tools
