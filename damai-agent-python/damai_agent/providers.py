"""Model provider adapters.

The OpenAI-compatible adapter uses Chat Completions tool calling so it can be
connected to multiple model vendors without coupling the Agent core to an SDK.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import urllib.error
import urllib.request
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Protocol, Sequence

from .models import (
    ChatMessage,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamEventType,
    ProviderUsage,
    ToolCall,
    ToolSpec,
)


class ModelProvider(Protocol):
    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse: ...


class ProviderError(RuntimeError):
    """Raised when the configured model endpoint cannot return a valid reply."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class _ToolCallAccumulator:
    tool_call_id: str = ""
    name: str = ""
    arguments: str = ""


class ProviderStreamAccumulator:
    """Build one provider response from standardized streaming events."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._refusal: list[str] = []
        self._tool_calls: Dict[int, _ToolCallAccumulator] = {}
        self._usage = ProviderUsage()
        self._finish_reason = "stop"
        self._model_route = ""

    def add(self, event: ProviderStreamEvent) -> None:
        if event.event_type is ProviderStreamEventType.TEXT_DELTA:
            self._text.append(event.text_delta)
        elif event.event_type is ProviderStreamEventType.TOOL_CALL_DELTA:
            state = self._tool_calls.setdefault(event.tool_call_index, _ToolCallAccumulator())
            state.tool_call_id = event.tool_call_id or state.tool_call_id
            state.name = event.tool_name or state.name
            state.arguments += event.arguments_delta
        elif event.event_type is ProviderStreamEventType.USAGE:
            self._usage = event.usage
        elif event.event_type is ProviderStreamEventType.COMPLETED:
            self._finish_reason = event.finish_reason or self._finish_reason
        if event.refusal:
            self._refusal.append(event.refusal)
        if event.model_route:
            self._model_route = event.model_route

    def build(self) -> ProviderResponse:
        tool_calls = []
        for index in sorted(self._tool_calls):
            state = self._tool_calls[index]
            try:
                arguments = json.loads(state.arguments or "{}")
            except json.JSONDecodeError as error:
                raise ProviderError("模型流式 Tool 参数不是合法 JSON") from error
            if not isinstance(arguments, dict) or not state.name:
                raise ProviderError("模型流式 Tool Call 结构不完整")
            tool_calls.append(
                ToolCall(
                    id=state.tool_call_id or str(uuid.uuid4()),
                    name=state.name,
                    arguments=arguments,
                )
            )
        content = "".join(self._text) or None
        refusal = "".join(self._refusal) or None
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            usage=self._usage,
            finish_reason=self._finish_reason,
            model_route=self._model_route,
            refusal=refusal,
        )


@dataclass(frozen=True, slots=True)
class _StreamFailure:
    error: BaseException


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        stream_idle_timeout_seconds: float = 15.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._stream_idle_timeout_seconds = stream_idle_timeout_seconds

    @property
    def route_name(self) -> str:
        return f"openai-compatible/{self._model}"

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        return await asyncio.to_thread(self._complete_sync, messages, tools)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderStreamEvent]:
        event_queue: queue.Queue[object] = queue.Queue()
        sentinel = object()

        def produce() -> None:
            try:
                for event in self._stream_sync(messages, tools):
                    event_queue.put(event)
            except BaseException as error:
                event_queue.put(_StreamFailure(error))
            finally:
                event_queue.put(sentinel)

        producer = asyncio.create_task(asyncio.to_thread(produce))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ProviderError("模型流式响应超过总超时", retryable=True)
                wait_seconds = min(self._stream_idle_timeout_seconds, remaining)
                try:
                    item = await asyncio.wait_for(
                        asyncio.to_thread(event_queue.get),
                        timeout=wait_seconds,
                    )
                except asyncio.TimeoutError as error:
                    raise ProviderError("模型流式响应空闲超时", retryable=True) from error
                if item is sentinel:
                    break
                if isinstance(item, _StreamFailure):
                    if isinstance(item.error, ProviderError):
                        raise item.error
                    raise ProviderError("模型流式响应读取失败") from item.error
                if isinstance(item, ProviderStreamEvent):
                    yield item
        finally:
            if producer.done():
                await producer
            else:
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer

    def _stream_sync(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> Iterator[ProviderStreamEvent]:
        payload = self._payload(messages, tools)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        request = self._request(payload)
        saw_completed = False
        saw_done = False
        socket_timeout = min(self._timeout_seconds, self._stream_idle_timeout_seconds)
        try:
            with urllib.request.urlopen(request, timeout=socket_timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="strict").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise ProviderError("模型流式响应包含非法 JSON") from error
                    for event in self._events_from_chunk(chunk):
                        if event.event_type is ProviderStreamEventType.COMPLETED:
                            saw_completed = True
                        yield event
        except urllib.error.HTTPError as error:
            raise ProviderError(
                f"模型服务返回 HTTP {error.code}",
                retryable=error.code in {408, 429, 500, 502, 503, 504},
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("模型流式调用失败", retryable=True) from error
        except UnicodeDecodeError as error:
            raise ProviderError("模型流式响应编码无效") from error
        if not saw_done:
            raise ProviderError("模型流式响应提前中断", retryable=True)
        if not saw_completed:
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.COMPLETED,
                finish_reason="stop",
                model_route=self.route_name,
            )

    def _events_from_chunk(self, chunk: Any) -> Iterator[ProviderStreamEvent]:
        if not isinstance(chunk, dict):
            raise ProviderError("模型流式响应结构无法识别")
        model_route = f"openai-compatible/{chunk.get('model') or self._model}"
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise ProviderError("模型流式 choices 结构无法识别")
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ProviderError("模型流式 delta 结构无法识别")
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield ProviderStreamEvent(
                    event_type=ProviderStreamEventType.TEXT_DELTA,
                    text_delta=content,
                    model_route=model_route,
                )
            refusal = delta.get("refusal")
            tool_calls = delta.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                raise ProviderError("模型流式 Tool Call 结构无法识别")
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    function = {}
                yield ProviderStreamEvent(
                    event_type=ProviderStreamEventType.TOOL_CALL_DELTA,
                    tool_call_index=int(tool_call.get("index") or 0),
                    tool_call_id=str(tool_call.get("id") or ""),
                    tool_name=str(function.get("name") or ""),
                    arguments_delta=str(function.get("arguments") or ""),
                    model_route=model_route,
                    refusal=str(refusal) if refusal else None,
                )
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                yield ProviderStreamEvent(
                    event_type=ProviderStreamEventType.COMPLETED,
                    finish_reason=str(finish_reason),
                    model_route=model_route,
                    refusal=str(refusal) if refusal else None,
                )
            elif refusal and not tool_calls:
                yield ProviderStreamEvent(
                    event_type=ProviderStreamEventType.TEXT_DELTA,
                    model_route=model_route,
                    refusal=str(refusal),
                )
        if chunk.get("usage") is not None:
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.USAGE,
                usage=self._parse_usage(chunk.get("usage")),
                model_route=model_route,
            )

    def _complete_sync(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        request = self._request(self._payload(messages, tools))
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ProviderError(
                f"模型服务返回 HTTP {error.code}",
                retryable=error.code in {408, 429, 500, 502, 503, 504},
            ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("模型服务调用失败", retryable=True) from error
        except json.JSONDecodeError as error:
            raise ProviderError("模型服务返回了非法 JSON") from error

        try:
            choice = body["choices"][0]
            message = choice["message"]
            tool_calls = [self._parse_tool_call(item) for item in message.get("tool_calls", [])]
            return ProviderResponse(
                content=message.get("content"),
                tool_calls=tool_calls,
                usage=self._parse_usage(body.get("usage")),
                finish_reason=str(choice.get("finish_reason") or "stop"),
                model_route=f"openai-compatible/{body.get('model') or self._model}",
                refusal=message.get("refusal"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("模型服务返回了无法识别的数据结构") from error

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> Dict[str, Any]:
        return {
            "model": self._model,
            "messages": [message.to_provider_dict() for message in messages],
            "tools": [tool.to_provider_dict() for tool in tools],
            "tool_choice": "auto",
            "temperature": 0.1,
        }

    def _request(self, payload: Dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            self._url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

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

    def _parse_usage(self, value: Any) -> ProviderUsage:
        if not isinstance(value, dict):
            return ProviderUsage()
        prompt_details = value.get("prompt_tokens_details")
        completion_details = value.get("completion_tokens_details")
        return ProviderUsage(
            prompt_tokens=int(value.get("prompt_tokens") or 0),
            completion_tokens=int(value.get("completion_tokens") or 0),
            cached_tokens=(
                int(prompt_details.get("cached_tokens") or 0)
                if isinstance(prompt_details, dict)
                else 0
            ),
            reasoning_tokens=(
                int(completion_details.get("reasoning_tokens") or 0)
                if isinstance(completion_details, dict)
                else 0
            ),
        )


class DemoProvider:
    """A deterministic provider for local integration before an LLM is configured.

    It still travels through the same Agent loop and Java tools, making it useful
    for verifying the boundary between Python and Java.
    """

    @property
    def route_name(self) -> str:
        return "demo/deterministic"

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

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ProviderStreamEvent]:
        response = await self.complete(messages, tools)
        if response.content:
            for start in range(0, len(response.content), 24):
                await asyncio.sleep(0)
                yield ProviderStreamEvent(
                    event_type=ProviderStreamEventType.TEXT_DELTA,
                    text_delta=response.content[start : start + 24],
                    model_route=self.route_name,
                )
        for index, tool_call in enumerate(response.tool_calls):
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.TOOL_CALL_DELTA,
                tool_call_index=index,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments_delta=json.dumps(
                    tool_call.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                model_route=self.route_name,
            )
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.USAGE,
            usage=response.usage,
            model_route=self.route_name,
        )
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.COMPLETED,
            finish_reason=response.finish_reason,
            model_route=self.route_name,
            refusal=response.refusal,
        )

    def _call(self, name: str, arguments: Dict[str, Any]) -> ProviderResponse:
        return ProviderResponse(
            tool_calls=[
                ToolCall(id=f"demo-{uuid.uuid4().hex[:12]}", name=name, arguments=arguments)
            ],
            finish_reason="tool_calls",
            model_route=self.route_name,
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
        if message.name == "recommend_programs":
            return self._summarize_recommendations(data)
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

    def _summarize_recommendations(self, data: Any) -> str:
        items = data.get("list", []) if isinstance(data, dict) else []
        if not items:
            return "没有找到同时满足条件且已核验有实时余票的候选；我不会自动放宽条件。"
        lines = ["以下候选均已核验实时票档余量（余量仍会随时变化）："]
        for item in items:
            lines.append(
                f"- 第 {item.get('rank', '?')} 名：{item.get('title', '未命名演出')}"
                f"（ID {item.get('id')}），最低可售票价 {item.get('lowestAvailablePrice')} 元，"
                f"当前总余量 {item.get('totalRemaining')}，理由 "
                f"{','.join(item.get('reasonCodes', []))}"
            )
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
