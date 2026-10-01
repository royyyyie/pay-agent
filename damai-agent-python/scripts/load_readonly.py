"""Bounded, payload-free load probe for the read-only staging ingress."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from damai_agent.evidence import write_report


@dataclass(frozen=True)
class Sample:
    status: int
    duration_ms: float


def percentile(values: list[float], percentage: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentage))
    return ordered[min(rank - 1, len(ordered) - 1)]


async def execute(args: argparse.Namespace) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv(args.api_key_env, "")
    if api_key:
        headers["X-Agent-Internal-Key"] = api_key
    timeout = httpx.Timeout(args.timeout)

    samples: list[Sample] = []
    next_index = 0

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        deadline = started + args.duration_seconds if args.duration_seconds else None

        async def request(index: int) -> Sample:
            request_id = uuid.uuid4().hex
            payload = {
                "message": args.message,
                "sessionKey": f"phase7-{request_id}-{index}",
            }
            started = time.perf_counter()
            try:
                response = await client.post(args.url, headers=headers, json=payload)
                status_code = response.status_code
            except httpx.HTTPError:
                status_code = 0
            return Sample(status_code, (time.perf_counter() - started) * 1000)

        async def worker() -> None:
            nonlocal next_index
            while next_index < args.requests and (
                deadline is None or time.perf_counter() < deadline
            ):
                index = next_index
                scheduled = started + index / args.rate_rps if args.rate_rps else started
                if deadline is not None and scheduled >= deadline:
                    return
                next_index += 1
                if scheduled > time.perf_counter():
                    await asyncio.sleep(scheduled - time.perf_counter())
                samples.append(await request(index))

        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        if deadline is not None and next_index < args.requests:
            await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
        elapsed = max(0.000001, time.perf_counter() - started)
        finished_at = datetime.now(timezone.utc)

    durations = [sample.duration_ms for sample in samples]
    successful = sum(1 for sample in samples if 200 <= sample.status < 300)
    success_rate = successful / len(samples) if samples else 0.0
    completed_window = deadline is None or elapsed >= args.duration_seconds
    return {
        "schemaVersion": "damai.phase7.load.v1",
        "target": f"{urlparse(args.url).scheme}://{urlparse(args.url).netloc}",
        "windowStart": started_at.isoformat().replace("+00:00", "Z"),
        "windowEnd": finished_at.isoformat().replace("+00:00", "Z"),
        "elapsedSeconds": round(elapsed, 3),
        "targetDurationSeconds": args.duration_seconds,
        "targetRateRps": args.rate_rps,
        "completedWindow": completed_window,
        "requests": len(samples),
        "concurrency": args.concurrency,
        "successRate": round(success_rate, 6),
        "throughputRps": round(len(samples) / elapsed, 3),
        "latencyMs": {
            "mean": round(statistics.fmean(durations), 3),
            "p50": round(percentile(durations, 0.50), 3),
            "p95": round(percentile(durations, 0.95), 3),
            "p99": round(percentile(durations, 0.99), 3),
        },
        "statusCounts": dict(sorted(Counter(sample.status for sample in samples).items())),
        "passed": completed_window
        and len(samples) >= args.min_requests
        and success_rate >= args.min_success_rate
        and percentile(durations, 0.95) <= args.max_p95_ms,
        "thresholds": {
            "minRequests": args.min_requests,
            "minSuccessRate": args.min_success_rate,
            "maxP95Ms": args.max_p95_ms,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded read-only load probe without logging payloads or credentials."
    )
    parser.add_argument("--url", required=True, help="Approved staging /api/v1/chat URL")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--duration-seconds", type=float, default=0)
    parser.add_argument("--rate-rps", type=float, default=0)
    parser.add_argument("--min-requests", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--message", default="查询北京本周可售演出")
    parser.add_argument("--api-key-env", default="DAMAI_AGENT_LOADTEST_API_KEY")
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--max-p95-ms", type=float, default=5000)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--allow-insecure-http", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("--url must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and not (
        args.allow_insecure_http and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        parser.error("non-TLS load probes are allowed only for explicit localhost tests")
    if not 1 <= args.requests <= 100000:
        parser.error("--requests must be between 1 and 100000")
    if not 1 <= args.concurrency <= min(args.requests, 1000):
        parser.error("--concurrency must be between 1 and requests, capped at 1000")
    if not math.isfinite(args.duration_seconds) or not 0 <= args.duration_seconds <= 3600:
        parser.error("--duration-seconds must be between 0 and 3600")
    if not math.isfinite(args.rate_rps) or not 0 <= args.rate_rps <= 10000:
        parser.error("--rate-rps must be between 0 and 10000")
    if not 1 <= args.min_requests <= args.requests:
        parser.error("--min-requests must be between 1 and requests")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        parser.error("--timeout must be in (0, 300]")
    if not math.isfinite(args.min_success_rate) or not 0 < args.min_success_rate <= 1:
        parser.error("--min-success-rate must be in (0, 1]")
    if not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0:
        parser.error("--max-p95-ms must be positive")
    return args


def main() -> int:
    args = parse_args()
    report = asyncio.run(execute(args))
    if args.report_out is not None:
        write_report(args.report_out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
