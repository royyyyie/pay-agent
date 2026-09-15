"""Model provider adapters.

The OpenAI-compatible adapter uses Chat Completions tool calling so it can be
connected to multiple model vendors without coupling the Agent core to an SDK.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional, Protocol, Sequence

from .models import ChatMessage, ProviderResponse, ToolCall, ToolSpec


class ModelProvider(Protocol):
    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse: ...


class ProviderError(RuntimeError):
    """Raised when the configured model endpoint cannot return a valid reply."""


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        return await asyncio.to_thread(self._complete_sync, messages, tools)

    def _complete_sync(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        payload = {
            "model": self._model,
            "messages": [message.to_provider_dict() for message in messages],
            "tools": [tool.to_provider_dict() for tool in tools],
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise ProviderError(f"模型服务返回 HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError(f"模型服务调用失败: {error}") from error

        try:
            message = body["choices"][0]["message"]
            tool_calls = [self._parse_tool_call(item) for item in message.get("tool_calls", [])]
            return ProviderResponse(content=message.get("content"), tool_calls=tool_calls)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("模型服务返回了无法识别的数据结构") from error

    def _parse_tool_call(self, item: Dict[str, Any]) -> ToolCall:
        function = item["function"]
        raw_arguments = function.get("arguments") or "{}"
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return ToolCall(
            id=item.get("id") or str(uuid.uuid4()),
            name=function["name"],
            arguments=arguments,
        )


class DemoProvider:
    """A deterministic provider for local integration before an LLM is configured.

    It still travels through the same Agent loop and Java tools, making it useful
    for verifying the boundary between Python and Java.
    """

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        last = messages[-1]
        if last.role == "tool":
            return ProviderResponse(content=self._summarize_tool(last))

        user_text = next(
            (message.content or "" for message in reversed(messages) if message.role == "user"),
            "",
        )
        program_id = self._extract_program_id(user_text)
        if program_id is not None and any(
            word in user_text for word in ("票价", "票档", "余票", "库存")
        ):
            return self._call("list_ticket_categories", {"programId": program_id})
        if program_id is not None and any(
            word in user_text for word in ("详情", "规则", "介绍", "信息")
        ):
            return self._call("get_program_detail", {"programId": program_id})
        keyword = self._extract_keyword(user_text)
        return self._call(
            "search_programs",
            {"keyword": keyword, "pageNumber": 1, "pageSize": 5, "timeType": 0},
        )

    def _call(self, name: str, arguments: Dict[str, Any]) -> ProviderResponse:
        return ProviderResponse(
            tool_calls=[
                ToolCall(id=f"demo-{uuid.uuid4().hex[:12]}", name=name, arguments=arguments)
            ]
        )

    def _extract_program_id(self, text: str) -> Optional[int]:
        match = re.search(r"(?:节目|演出)?\s*(?:id|ID|编号)?\s*[：:#]?\s*(\d{3,})", text)
        return int(match.group(1)) if match else None

    def _extract_keyword(self, text: str) -> str:
        cleaned = re.sub(
            r"(请问|帮我|查一下|查询|搜索|看看|有哪些|有没有|的演唱会|演唱会|演出|节目|门票|推荐)",
            " ",
            text,
        )
        cleaned = re.sub(r"[？?，,。！!]", " ", cleaned)
        return " ".join(cleaned.split())[:100] or text.strip()[:100]

    def _summarize_tool(self, message: ChatMessage) -> str:
        try:
            result = json.loads(message.content or "{}")
        except json.JSONDecodeError:
            return "工具返回的数据无法解析，请稍后重试。"
        if not result.get("success"):
            return f"查询失败：{result.get('message') or '业务服务暂时不可用'}"
        data = result.get("data")
        if message.name == "search_programs":
            return self._summarize_search(data)
        if message.name == "get_program_detail":
            return self._summarize_detail(data)
        if message.name == "list_ticket_categories":
            return self._summarize_ticket_categories(data)
        return json.dumps(data, ensure_ascii=False, default=str)

    def _summarize_search(self, data: Any) -> str:
        items = data.get("list", []) if isinstance(data, dict) else []
        if not items:
            return "没有查到符合条件的演出。可以换一个节目名、艺人或城市再试。"
        lines = [f"查到 {data.get('totalSize', len(items))} 场相关演出，前几项是："]
        for item in items[:5]:
            price = self._price_range(item)
            lines.append(
                f"- {item.get('title', '未命名演出')}（ID {item.get('id')}），"
                f"{item.get('place') or '场馆待定'}，{item.get('showTime') or '时间待定'}{price}"
            )
        lines.append("告诉我节目 ID，我可以继续查询详情或票档余量。")
        return "\n".join(lines)

    def _summarize_detail(self, data: Any) -> str:
        if not isinstance(data, dict):
            return "没有查到该节目的详情。"
        lines = [
            f"{data.get('title', '未命名演出')}（ID {data.get('id')}）",
            f"艺人/主演：{data.get('actor') or data.get('mainActor') or '待公布'}",
            f"地点：{data.get('place') or '待公布'}",
            f"时间：{data.get('showTime') or '待公布'}",
        ]
        if data.get("importantNotice"):
            lines.append(f"重要提示：{data['importantNotice']}")
        if data.get("refundTicketRule"):
            lines.append(f"退换规则：{data['refundTicketRule']}")
        return "\n".join(lines)

    def _summarize_ticket_categories(self, data: Any) -> str:
        if not isinstance(data, list) or not data:
            return "该节目暂未查到可用票档。"
        lines = ["当前票档与余量如下（余量会实时变化）："]
        for item in data:
            lines.append(
                f"- {item.get('introduce') or '普通票档'}：¥{item.get('price')}，"
                f"剩余 {item.get('remainNumber', '未知')} 张"
            )
        return "\n".join(lines)

    def _price_range(self, item: Dict[str, Any]) -> str:
        minimum = item.get("minPrice")
        maximum = item.get("maxPrice")
        if minimum is None:
            return ""
        if maximum is None or maximum == minimum:
            return f"，¥{minimum} 起"
        return f"，¥{minimum}–¥{maximum}"
