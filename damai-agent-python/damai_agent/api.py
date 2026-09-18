"""FastAPI transport for synchronous and SSE Agent turns."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .delegation import DelegationError, verify_delegation
from .models import AgentRunResult, TicketTurnContext
from .provider_routing import ResilientProvider
from .providers import DemoProvider, ModelProvider, OpenAICompatibleProvider, ProviderError
from .runner import AgentRunner
from .session import InMemorySessionStore
from .tools import JavaToolClient, ToolRegistry, build_java_tools


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    sessionKey: Optional[str] = Field(default=None, max_length=200)


class ChatResponse(BaseModel):
    sessionKey: str
    turnId: str
    traceId: str
    answer: str
    toolCalls: list[str]
    usage: Dict[str, int]
    stopReason: str
    errorCode: Optional[str]
    modelRoute: str


class DurableChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    sessionKey: str = Field(min_length=1, max_length=200)


class RecoverRequest(BaseModel):
    sessionKey: str = Field(min_length=1, max_length=200)
    message: Optional[str] = Field(default=None, min_length=1, max_length=2000)


def _chat_response(result: AgentRunResult) -> ChatResponse:
    return ChatResponse(
        sessionKey=result.session_key,
        turnId=result.turn_id,
        traceId=result.trace_id,
        answer=result.answer,
        toolCalls=result.tool_calls,
        usage=result.usage.to_dict(),
        stopReason=result.stop_reason,
        errorCode=result.error_code.value if result.error_code else None,
        modelRoute=result.model_route,
    )


def build_runner(settings: Settings) -> AgentRunner:
    provider: ModelProvider
    if settings.provider == "openai_compatible":
        primary = OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            stream_idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        )
        fallback = (
            OpenAICompatibleProvider(
                base_url=settings.llm_fallback_base_url,
                api_key=settings.llm_fallback_api_key,
                model=settings.llm_fallback_model,
                timeout_seconds=settings.llm_timeout_seconds,
                stream_idle_timeout_seconds=settings.stream_idle_timeout_seconds,
            )
            if settings.llm_fallback_base_url
            else None
        )
        provider = (
            ResilientProvider(
                primary,
                fallback,
                max_retries=settings.llm_max_retries,
                backoff_seconds=settings.llm_retry_backoff_seconds,
            )
            if fallback is not None or settings.llm_max_retries
            else primary
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
        max_concurrent_read_tools=settings.max_concurrent_read_tools,
        tool_timeout_seconds=settings.tool_timeout_seconds,
        max_context_chars=settings.max_context_chars,
        max_tool_result_chars=settings.max_tool_result_chars,
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runner = build_runner(resolved_settings)
    durable_service = None
    durable_turns = None
    durable_redis = None
    if resolved_settings.runtime_backend == "durable":
        from redis.asyncio import Redis

        from .postgres_turn import PostgresTurnRepository
        from .redis_control import RedisTurnCancellationStore
        from .redis_lease import RedisSessionLeaseStore
        from .redis_queue import RedisPendingTurnQueue
        from .runtime.durable import DurableTurnService

        durable_redis = Redis.from_url(
            resolved_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        durable_turns = PostgresTurnRepository(resolved_settings.postgres_dsn)
        durable_service = DurableTurnService(
            runner.core_runner,
            durable_turns,
            RedisSessionLeaseStore(durable_redis),
            cancellations=RedisTurnCancellationStore(durable_redis),
            pending_queue=RedisPendingTurnQueue(durable_redis),
            max_tool_rounds=resolved_settings.max_tool_rounds,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if durable_redis is not None:
                await durable_redis.aclose()

    app = FastAPI(
        title="Damai Agent API",
        description="Java 业务系统 + Python Agent 的只读运行时",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.runner = runner
    app.state.settings = resolved_settings
    app.state.durable_service = durable_service
    app.state.durable_turns = durable_turns
    app.state.durable_redis = durable_redis

    async def require_internal_api_key(
        supplied_key: Optional[str] = Header(default=None, alias="X-Agent-Internal-Key"),
    ) -> None:
        if not resolved_settings.requires_internal_auth:
            return
        expected_key = resolved_settings.internal_api_key
        if supplied_key is None or not hmac.compare_digest(expected_key, supplied_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent API 鉴权失败",
            )

    async def require_delegation(
        encoded: Optional[str] = Header(default=None, alias="X-Agent-Delegation"),
        signature: Optional[str] = Header(default=None, alias="X-Agent-Delegation-Signature"),
    ) -> TicketTurnContext:
        if encoded is None or signature is None or durable_service is None:
            raise HTTPException(status_code=401, detail="Agent 委托身份无效")
        try:
            return verify_delegation(encoded, signature, resolved_settings.delegation_hmac_key)
        except DelegationError as exc:
            raise HTTPException(status_code=401, detail="Agent 委托身份无效") from exc

    def check_durable_request(request: DurableChatRequest, context: TicketTurnContext) -> None:
        if request.sessionKey != context.session_key:
            raise HTTPException(status_code=403, detail="Session 与委托身份不匹配")

    def durable_error(error: Exception) -> HTTPException:
        from .postgres_turn import TurnConflict
        from .runtime.durable import LeaseLost, TurnCancelled

        if isinstance(error, TurnConflict):
            return HTTPException(status_code=409, detail={"code": "TURN_CONFLICT"})
        if isinstance(error, TurnCancelled):
            return HTTPException(status_code=409, detail={"code": "TURN_CANCELLED"})
        if isinstance(error, ProviderError):
            return HTTPException(status_code=502, detail={"code": "PROVIDER_UNAVAILABLE"})
        if isinstance(error, LeaseLost):
            return HTTPException(status_code=503, detail={"code": "LEASE_LOST"})
        return HTTPException(status_code=503, detail={"code": "PERSISTENCE_UNAVAILABLE"})

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "UP",
            "environment": resolved_settings.environment.value,
            "provider": resolved_settings.provider,
            "tools": runner.tool_names,
        }

    @app.get("/ready")
    async def ready() -> Dict[str, str]:
        if durable_turns is not None and durable_redis is not None:
            try:
                database_ready = await durable_turns.check_ready()
                cache_ready = bool(await durable_redis.ping())
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Agent 持久化依赖不可用") from exc
            if not database_ready or not cache_ready:
                raise HTTPException(status_code=503, detail="Agent 持久化依赖不可用")
        return {"status": "UP"}

    @app.post(
        "/api/v1/chat",
        response_model=ChatResponse,
        dependencies=[Depends(require_internal_api_key)],
    )
    async def chat(request: ChatRequest) -> ChatResponse:
        if resolved_settings.runtime_backend == "durable":
            raise HTTPException(status_code=410, detail="请使用受信的 /api/v2/turns 入口")
        try:
            result = await runner.run(request.message, request.sessionKey)
        except ProviderError as error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "PROVIDER_UNAVAILABLE",
                    "message": "模型服务暂时不可用，请稍后重试",
                },
            ) from error
        return _chat_response(result)

    @app.post(
        "/api/v1/chat/stream",
        dependencies=[Depends(require_internal_api_key)],
    )
    async def stream_chat(request: ChatRequest) -> StreamingResponse:
        if resolved_settings.runtime_backend == "durable":
            raise HTTPException(status_code=410, detail="请使用受信的 /api/v2/turns 入口")

        async def event_stream() -> AsyncIterator[str]:
            queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()

            async def sink(event: Dict[str, Any]) -> None:
                await queue.put(event)

            async def execute() -> None:
                try:
                    await runner.run(request.message, request.sessionKey, sink)
                except ProviderError:
                    await queue.put(
                        {
                            "type": "turn.failed",
                            "code": "PROVIDER_UNAVAILABLE",
                            "message": "模型服务暂时不可用，请稍后重试",
                        }
                    )
                except Exception:
                    await queue.put(
                        {
                            "type": "turn.failed",
                            "code": "INTERNAL_ERROR",
                            "message": "Agent 执行失败，请稍后重试",
                        }
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(execute())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(),
                            timeout=resolved_settings.stream_idle_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        timeout_event = {
                            "type": "turn.failed",
                            "code": "STREAM_IDLE_TIMEOUT",
                            "message": "Agent 流式响应超时，请稍后重试",
                        }
                        data = json.dumps(timeout_event, ensure_ascii=False)
                        yield f"event: turn.failed\ndata: {data}\n\n"
                        break
                    if event is None:
                        break
                    event_name = event.get("type", "message")
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"event: {event_name}\ndata: {data}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/v2/turns", dependencies=[Depends(require_internal_api_key)])
    async def durable_chat(
        request: DurableChatRequest,
        response: Response,
        context: TicketTurnContext = Depends(require_delegation),  # noqa: B008
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        from .runtime.durable import SessionBusy

        check_durable_request(request, context)
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key 无效")
        assert durable_service is not None
        try:
            result = await durable_service.run(request.message, context, idempotency_key)
        except SessionBusy:
            try:
                position = await durable_service.enqueue(request.message, context, idempotency_key)
            except Exception as exc:
                raise durable_error(exc) from exc
            response.status_code = 202
            return {
                "status": "pending",
                "sessionKey": context.session_key,
                "turnId": context.turn_id,
                "position": position,
            }
        except Exception as exc:
            raise durable_error(exc) from exc
        return _chat_response(result).model_dump()

    @app.post("/api/v2/turns/recover", dependencies=[Depends(require_internal_api_key)])
    async def recover_turn(
        request: RecoverRequest,
        context: TicketTurnContext = Depends(require_delegation),  # noqa: B008
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> ChatResponse:
        if request.sessionKey != context.session_key:
            raise HTTPException(status_code=403, detail="Session 与委托身份不匹配")
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key 无效")
        assert durable_service is not None
        try:
            result = await durable_service.recover(request.message, context, idempotency_key)
        except Exception as exc:
            raise durable_error(exc) from exc
        return _chat_response(result)

    @app.post("/api/v2/turns/cancel", dependencies=[Depends(require_internal_api_key)])
    async def cancel_turn(
        request: DurableChatRequest,
        context: TicketTurnContext = Depends(require_delegation),  # noqa: B008
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, bool]:
        check_durable_request(request, context)
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key 无效")
        assert durable_service is not None
        try:
            requested = await durable_service.cancel(request.message, context, idempotency_key)
        except Exception as exc:
            raise durable_error(exc) from exc
        return {"cancelRequested": requested}

    @app.get(
        "/api/v2/turns/{turn_id}/events",
        dependencies=[Depends(require_internal_api_key)],
    )
    async def replay_events(
        turn_id: str,
        sessionKey: str,
        request: Request,
        context: TicketTurnContext = Depends(require_delegation),  # noqa: B008
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if context.turn_id != turn_id or context.session_key != sessionKey:
            raise HTTPException(status_code=403, detail="Turn 与委托身份不匹配")
        if last_event_id is not None and (not last_event_id.isdecimal() or len(last_event_id) > 19):
            raise HTTPException(status_code=400, detail="Last-Event-ID 无效")
        cursor = int(last_event_id) if last_event_id else 0
        if cursor >= 2**63:
            raise HTTPException(status_code=400, detail="Last-Event-ID 无效")
        assert durable_turns is not None
        current_status = await durable_turns.get_turn_status(
            context.tenant_id, context.session_key, turn_id
        )
        if current_status is None:
            raise HTTPException(status_code=404, detail="Turn 不存在")

        async def replay() -> AsyncIterator[str]:
            nonlocal cursor
            token_remaining = (
                max(0, context.delegation_expires_at - time.time())
                if context.delegation_expires_at is not None
                else 300
            )
            deadline = time.monotonic() + min(300, token_remaining)
            while time.monotonic() < deadline and not await request.is_disconnected():
                events = await durable_turns.load_events_after(
                    context.tenant_id, context.session_key, turn_id, cursor
                )
                for event in events:
                    cursor = event["eventSeq"]
                    data = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n"
                if len(events) == 200:
                    continue
                if (
                    await durable_turns.get_turn_status(
                        context.tenant_id, context.session_key, turn_id
                    )
                    == "completed"
                ):
                    break
                if not events:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(resolved_settings.event_poll_seconds)

        return StreamingResponse(replay(), media_type="text/event-stream")

    return app


app = create_app()
