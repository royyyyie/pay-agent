"""Child process for isolated real-process interruption acceptance tests."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Sequence

from redis.asyncio import Redis

from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    TicketTurnContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from damai_agent.postgres_turn import PostgresTurnRepository
from damai_agent.redis_lease import RedisSessionLeaseStore
from damai_agent.runtime.durable import DurableTurnService
from damai_agent.runtime.runner import ToolCallingRunner
from damai_agent.tools import AgentTool, ToolRegistry


class OneCallProvider:
    route_name = "test/killable"

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        return ProviderResponse(
            tool_calls=[ToolCall("kill-call-1", "search_programs", {})],
            finish_reason="tool_calls",
            model_route=self.route_name,
        )


class BlockingReadTool(AgentTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="blocking read-only acceptance tool",
            parameters={"type": "object", "properties": {}},
            required_scope="programs:read",
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        await asyncio.sleep(30)
        return ToolResult(True, 0, "unexpected completion")


async def main(session_key: str, key_prefix: str) -> None:
    client = Redis(
        host=os.environ["DAMAI_TEST_REDIS_HOST"],
        port=int(os.environ.get("DAMAI_TEST_REDIS_PORT", "6379")),
        username=os.environ.get("DAMAI_TEST_REDIS_USERNAME") or None,
        password=os.environ.get("DAMAI_TEST_REDIS_PASSWORD") or None,
        decode_responses=True,
    )
    try:
        service = DurableTurnService(
            ToolCallingRunner(OneCallProvider(), ToolRegistry([BlockingReadTool()])),
            PostgresTurnRepository(os.environ["DAMAI_TEST_POSTGRES_DSN"]),
            RedisSessionLeaseStore(client, key_prefix=key_prefix),
            lease_ttl_ms=300,
        )
        context = TicketTurnContext(
            tenant_id="tenant-durable-test",
            user_id="user-1",
            session_key=session_key,
            turn_id="turn-killed",
            request_id="request-killed",
            trace_id="trace-killed",
            locale="zh-CN",
            channel="test",
            tool_scopes=frozenset({"programs:read"}),
            risk_ceiling=ToolRisk.READ_ONLY,
            delegation_token_id="test-delegation",
        )
        await service.run("查票", context, "idem-killed")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
