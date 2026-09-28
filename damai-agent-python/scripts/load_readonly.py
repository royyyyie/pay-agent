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
from urllib.parse import urlparse

import httpx


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
    semaphore = asyncio.Semaphore(args.concurrency)
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv(args.api_key_env, "")
    if api_key:
        headers["X-Agent-Internal-Key"] = api_key
    timeout = httpx.Timeout(args.timeout)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:

        async def request(index: int) -> Sample:
            request_id = uuid.uuid4().hex
            payload = {
                "message": args.message,
                "sessionKey": f"phase7-{request_id}-{index}",
            }
            started = time.perf_counter()
            try:
                async with semaphore:
                    response = await client.post(args.url, headers=headers, json=payload)
                status_code = response.status_code
            except httpx.HTTPError:
                status_code = 0
            return Sample(status_code, (time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        samples = await asyncio.gather(*(request(index) for index in range(args.requests)))
        elapsed = max(0.000001, time.perf_counter() - started)

    durations = [sample.duration_ms for sample in samples]
    successful = sum(1 for sample in samples if 200 <= sample.status < 300)
    success_rate = successful / len(samples)
    return {
        "schemaVersion": "damai.phase7.load.v1",
        "target": f"{urlparse(args.url).scheme}://{urlparse(args.url).netloc}",
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
        "passed": success_rate >= args.min_success_rate
        and percentile(durations, 0.95) <= args.max_p95_ms,
        "thresholds": {
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
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--message", default="查询北京本周可售演出")
    parser.add_argument("--api-key-env", default="DAMAI_AGENT_LOADTEST_API_KEY")
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--max-p95-ms", type=float, default=5000)
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
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be in (0, 300]")
    if not 0 < args.min_success_rate <= 1:
        parser.error("--min-success-rate must be in (0, 1]")
    if args.max_p95_ms <= 0:
        parser.error("--max-p95-ms must be positive")
    return args


def main() -> int:
    report = asyncio.run(execute(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
