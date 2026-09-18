"""Typed Agent event creation with stable sequencing."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from ..models import AgentEvent, TicketTurnContext

EventSink = Callable[[Dict[str, Any]], Optional[Awaitable[None]]]


class TurnEventEmitter:
    def __init__(self, context: TicketTurnContext, sink: Optional[EventSink]) -> None:
        self._context = context
        self._sink = sink
        self._sequence = 0

    async def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> AgentEvent:
        self._sequence += 1
        event = AgentEvent(
            event_type=event_type,
            turn_id=self._context.turn_id,
            trace_id=self._context.trace_id,
            session_key=self._context.session_key,
            sequence=self._sequence,
            payload=payload or {},
        )
        if self._sink is not None:
            possible_awaitable = self._sink(event.to_dict())
            if inspect.isawaitable(possible_awaitable):
                await possible_awaitable
        return event
