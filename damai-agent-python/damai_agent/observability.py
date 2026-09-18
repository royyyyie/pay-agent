"""Bounded, payload-free per-process metrics for Prometheus scraping."""

from __future__ import annotations

import threading
from typing import Literal

from .models import ProviderUsage

_BUCKETS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
_Outcome = Literal["success", "error", "rejected"]


class _Histogram:
    def __init__(self) -> None:
        self.count = 0
        self.total_seconds = 0.0
        self.buckets = [0] * len(_BUCKETS)

    def observe(self, duration_ms: int) -> None:
        seconds = max(0, duration_ms) / 1000
        self.count += 1
        self.total_seconds += seconds
        for index, upper in enumerate(_BUCKETS):
            if seconds <= upper:
                self.buckets[index] += 1


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, dict[_Outcome, int]] = {
            kind: {"success": 0, "error": 0, "rejected": 0} for kind in ("turn", "model", "tool")
        }
        self._durations = {kind: _Histogram() for kind in self._requests}
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cost_micro_usd = 0

    def observe_turn(self, outcome: _Outcome, duration_ms: int) -> None:
        with self._lock:
            self._requests["turn"][outcome] += 1
            self._durations["turn"].observe(duration_ms)

    def observe_tool(self, success: bool, duration_ms: int) -> None:
        self._observe("tool", success, duration_ms)

    def observe_model(
        self,
        success: bool,
        duration_ms: int,
        usage: ProviderUsage | None = None,
        cost_micro_usd: int | None = None,
    ) -> None:
        with self._lock:
            self._count("model", success, duration_ms)
            if usage is not None:
                self._prompt_tokens += max(0, usage.prompt_tokens)
                self._completion_tokens += max(0, usage.completion_tokens)
            if cost_micro_usd is not None:
                self._cost_micro_usd += max(0, cost_micro_usd)

    def _observe(self, kind: str, success: bool, duration_ms: int) -> None:
        with self._lock:
            self._count(kind, success, duration_ms)

    def _count(self, kind: str, success: bool, duration_ms: int) -> None:
        outcome: _Outcome = "success" if success else "error"
        self._requests[kind][outcome] += 1
        self._durations[kind].observe(duration_ms)

    def render_prometheus(self) -> str:
        with self._lock:
            lines = ["# HELP damai_agent_requests_total Agent operations by outcome."]
            lines.append("# TYPE damai_agent_requests_total counter")
            for kind, outcomes in self._requests.items():
                for outcome, count in outcomes.items():
                    lines.append(
                        f'damai_agent_requests_total{{kind="{kind}",outcome="{outcome}"}} {count}'
                    )
            lines.extend(
                (
                    "# HELP damai_agent_duration_seconds Agent operation duration.",
                    "# TYPE damai_agent_duration_seconds histogram",
                )
            )
            for kind, histogram in self._durations.items():
                for upper, count in zip(_BUCKETS, histogram.buckets, strict=True):
                    lines.append(
                        f'damai_agent_duration_seconds_bucket{{kind="{kind}",le="{upper:g}"}} '
                        f"{count}"
                    )
                lines.append(
                    f'damai_agent_duration_seconds_bucket{{kind="{kind}",le="+Inf"}} '
                    f"{histogram.count}"
                )
                lines.append(
                    f'damai_agent_duration_seconds_count{{kind="{kind}"}} {histogram.count}'
                )
                lines.append(
                    f'damai_agent_duration_seconds_sum{{kind="{kind}"}} {histogram.total_seconds:g}'
                )
            for name, value, description in (
                ("prompt_tokens", self._prompt_tokens, "Billed prompt tokens."),
                ("completion_tokens", self._completion_tokens, "Billed completion tokens."),
                ("cost_micro_usd", self._cost_micro_usd, "Estimated model cost in micro-USD."),
            ):
                lines.append(f"# HELP damai_agent_{name}_total {description}")
                lines.append(f"# TYPE damai_agent_{name}_total counter")
                lines.append(f"damai_agent_{name}_total {value}")
        return "\n".join(lines) + "\n"
