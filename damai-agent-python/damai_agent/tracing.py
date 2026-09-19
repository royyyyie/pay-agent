"""Opt-in OpenTelemetry spans with no prompt, user, or tool payload attributes."""

from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, TraceFlags, Tracer, TraceState
from opentelemetry.trace.status import Status, StatusCode

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider


class TraceManager:
    def __init__(
        self,
        tracer: Tracer | None = None,
        provider: TracerProvider | None = None,
    ) -> None:
        self._tracer = tracer
        self._provider = provider

    @classmethod
    def from_otlp(cls, endpoint: str) -> TraceManager:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "damai-agent-python"}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5))
        )
        return cls(provider.get_tracer("damai_agent"), provider)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        attributes: Mapping[str, str | int | bool] | None = None,
    ) -> Iterator[Span]:
        if self._tracer is None:
            yield trace.get_current_span()
            return
        parent_context = None
        if trace_id is not None and re.fullmatch(r"[0-9a-f]{32}", trace_id):
            supplied = int(trace_id, 16)
            if supplied:
                parent = NonRecordingSpan(
                    SpanContext(
                        trace_id=supplied,
                        span_id=secrets.randbits(64) or 1,
                        is_remote=True,
                        trace_flags=TraceFlags(TraceFlags.SAMPLED),
                        trace_state=TraceState(),
                    )
                )
                parent_context = trace.set_span_in_context(parent)
        with self._tracer.start_as_current_span(
            name,
            context=parent_context,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException:
                self.mark_error(span)
                raise

    @staticmethod
    def mark_error(span: Span) -> None:
        span.set_status(Status(StatusCode.ERROR))

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
