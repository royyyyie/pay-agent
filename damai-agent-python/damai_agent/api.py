"""FastAPI transport for synchronous and SSE Agent turns."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .providers import DemoProvider, OpenAICompatibleProvider, ProviderError
from .runner import AgentRunner
from .session import InMemorySessionStore
from .tools import JavaToolClient, ToolRegistry, build_java_tools


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    sessionKey: Optional[str] = Field(default=None, max_length=200)


class ChatResponse(BaseModel):
    sessionKey: str
    turnId: str
    answer: str
    toolCalls: list[str]


def build_runner(settings: Settings) -> AgentRunner:
    settings.validate()
    if settings.provider == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    else:
        provider = DemoProvider()

    java_client = JavaToolClient(
        base_url=settings.java_base_url,
        api_key=settings.java_tool_api_key,
        timeout_seconds=settings.java_timeout_seconds,
    )
    registry = ToolRegistry(build_java_tools(java_client))
    return AgentRunner(
        provider=provider,
        registry=registry,
        sessions=InMemorySessionStore(),
        max_tool_rounds=settings.max_tool_rounds,
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runner = build_runner(resolved_settings)
    app = FastAPI(
        title="Damai Agent API",
        description="Java 业务系统 + Python Agent 的第一版只读闭环",
        version="0.1.0",
    )
    app.state.runner = runner
    app.state.settings = resolved_settings

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "UP",
            "provider": resolved_settings.provider,
            "javaBaseUrl": resolved_settings.java_base_url,
            "tools": runner.tool_names,
        }

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        try:
            result = await runner.run(request.message, request.sessionKey)
        except ProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return ChatResponse(
            sessionKey=result.session_key,
            turnId=result.turn_id,
            answer=result.answer,
            toolCalls=result.tool_calls,
        )

    @app.post("/api/v1/chat/stream")
    async def stream_chat(request: ChatRequest) -> StreamingResponse:
        async def event_stream() -> AsyncIterator[str]:
            queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()

            async def sink(event: Dict[str, Any]) -> None:
                await queue.put(event)

            async def execute() -> None:
                try:
                    await runner.run(request.message, request.sessionKey, sink)
                except Exception as error:
                    await queue.put(
                        {"type": "turn.failed", "message": str(error)[:500]}
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(execute())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    event_name = event.get("type", "message")
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"event: {event_name}\ndata: {data}\n\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


app = create_app()
