"""Tool registry and Java Tool Gateway adapters."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List

from .models import ToolContext, ToolResult, ToolSpec


class AgentTool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self, arguments: Dict[str, Any], context: ToolContext
    ) -> ToolResult:
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
            return await asyncio.wait_for(
                tool.execute(arguments, context), timeout=timeout_seconds
            )
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

    async def post(
        self, path: str, payload: Dict[str, Any], context: ToolContext
    ) -> ToolResult:
        return await asyncio.to_thread(self._post_sync, path, payload, context)

    def _post_sync(
        self, path: str, payload: Dict[str, Any], context: ToolContext
    ) -> ToolResult:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Agent-Key": self._api_key,
                "X-Agent-Session-Key": context.session_key,
                "X-Agent-Turn-Id": context.turn_id,
                "X-Agent-Tool-Call-Id": context.tool_call_id,
                "traceId": context.trace_id,
            },
            method="POST",
        )
        status = 200
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                status = response.status
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            status = error.code
            body = error.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as error:
            return ToolResult(
                success=False,
                code=503,
                message=f"Java 业务服务不可用: {error.reason if hasattr(error, 'reason') else error}",
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

    async def execute(
        self, arguments: Dict[str, Any], context: ToolContext
    ) -> ToolResult:
        return await self._client.post(self._path, arguments, context)


def build_java_tools(client: JavaToolClient) -> List[AgentTool]:
    program_id_parameters = {
        "type": "object",
        "properties": {
            "programId": {
                "type": "integer",
                "description": "从节目搜索结果获得的节目 ID",
            }
        },
        "required": ["programId"],
        "additionalProperties": False,
    }
    return [
        JavaReadTool(
            client,
            ToolSpec(
                name="search_programs",
                description="按节目名、艺人、城市、分类和时间范围搜索演出。未知节目 ID 时先调用此工具。",
                parameters={
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "节目名或艺人关键词"},
                        "areaId": {"type": "integer", "description": "城市区域 ID"},
                        "parentProgramCategoryId": {"type": "integer"},
                        "programCategoryId": {"type": "integer"},
                        "timeType": {
                            "type": "integer",
                            "enum": [0, 1, 2, 3, 4, 5],
                            "description": "0 全部、1 今天、2 明天、3 一周内、4 一月内、5 自定义",
                        },
                        "startDateTime": {"type": "string", "description": "yyyy-MM-dd HH:mm:ss"},
                        "endDateTime": {"type": "string", "description": "yyyy-MM-dd HH:mm:ss"},
                        "sortType": {"type": "integer", "enum": [1, 2, 3, 4]},
                        "pageNumber": {"type": "integer", "minimum": 1},
                        "pageSize": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "additionalProperties": False,
                },
            ),
            "/internal/agent/v1/tools/programs/search",
        ),
        JavaReadTool(
            client,
            ToolSpec(
                name="get_program_detail",
                description="根据节目 ID 查询演出详情、购票规则和入场说明。",
                parameters=program_id_parameters,
            ),
            "/internal/agent/v1/tools/programs/detail",
        ),
        JavaReadTool(
            client,
            ToolSpec(
                name="list_ticket_categories",
                description="根据节目 ID 查询票档、价格与当前剩余数量。余量只代表查询时刻。",
                parameters=program_id_parameters,
            ),
            "/internal/agent/v1/tools/programs/ticket-categories",
        ),
    ]

