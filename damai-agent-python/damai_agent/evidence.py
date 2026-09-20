"""Shared bounded evidence helpers used by standalone acceptance commands."""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_COST_EVIDENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")


def usd_to_micro(value: str, *, label: str) -> int | None:
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a decimal USD amount") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        raise ValueError(f"{label} is outside the accepted range")
    return int((amount * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def bounded_cost_evidence(value: str) -> str:
    if value and _COST_EVIDENCE_PATTERN.fullmatch(value) is None:
        raise ValueError("cost evidence identifier is invalid")
    return value


def write_report(path: Path, payload: dict[str, object]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
