"""Fail-closed phase 7 staging and production-canary evidence gate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_REFERENCE = r"^[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,255}$"
_MAX_REPORT_BYTES = 4 * 1024 * 1024
_FAULT_NAMES = {
    "provider",
    "java_gateway",
    "redis",
    "postgresql",
    "elasticsearch",
    "kafka",
    "instance_kill",
}
_ROLLBACK_NAMES = {"application", "database", "knowledge_index", "backup_restore"}
_APPROVAL_ROLES = {"security", "sre", "business", "transaction"}
_CARRYOVER_PHASES = {"phase3", "phase4", "phase5", "phase6"}
_EVIDENCE_NAMES = {
    "metrics",
    "traces",
    "faults",
    "rollback",
    "security",
    "approvals",
    "phase3",
    "phase4",
    "phase5",
    "phase6",
}


class FaultEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    passed: bool
    evidence_reference: str = Field(pattern=_REFERENCE)
    recovery_time_seconds: int = Field(ge=0, le=3600)
    duplicate_side_effects: int = Field(ge=0, le=1_000_000)
    blind_write_retries: int = Field(ge=0, le=1_000_000)
    cross_tenant_successes: int = Field(ge=0, le=1_000_000)


class RollbackEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    passed: bool
    evidence_reference: str = Field(pattern=_REFERENCE)
    data_loss_events: int = Field(ge=0, le=1_000_000)


class SupplyChainEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    sbom_generated: bool
    provenance_attested: bool
    immutable_base_images: bool
    action_refs_pinned: bool
    critical_vulnerabilities: int = Field(ge=0, le=1_000_000)
    high_vulnerabilities: int = Field(ge=0, le=1_000_000)
    secrets_detected: int = Field(ge=0, le=1_000_000)
    iac_critical_findings: int = Field(ge=0, le=1_000_000)
    iac_high_findings: int = Field(ge=0, le=1_000_000)


class SafetyCounters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unauthorized_tool_successes: int = Field(ge=0, le=1_000_000)
    cross_tenant_successes: int = Field(ge=0, le=1_000_000)
    duplicate_orders: int = Field(ge=0, le=1_000_000)
    blind_unknown_write_replays: int = Field(ge=0, le=1_000_000)
    pii_or_secret_leaks: int = Field(ge=0, le=1_000_000)


class ApprovalEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    approved: bool
    approver_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,127}$")
    approval_reference: str = Field(pattern=_REFERENCE)
    approved_at: AwareDatetime


class Phase7ReleaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["damai.phase7.release/v1"]
    environment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    environment_tier: Literal["staging"]
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_repository: str = Field(pattern=r"^[a-z0-9][a-z0-9._/-]{2,254}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    window_start: AwareDatetime
    window_end: AwareDatetime
    request_count: int = Field(ge=1, le=100_000_000)
    peak_concurrency: int = Field(ge=1, le=1_000_000)
    success_rate: float = Field(ge=0, le=1)
    throughput_rps: float = Field(ge=0, le=1_000_000)
    p50_turn_latency_ms: float = Field(ge=0, le=3_600_000)
    p95_turn_latency_ms: float = Field(ge=0, le=3_600_000)
    p99_turn_latency_ms: float = Field(ge=0, le=3_600_000)
    trace_sample_count: int = Field(ge=0, le=10_000_000)
    trace_continuity_rate: float = Field(ge=0, le=1)
    supply_chain: SupplyChainEvidence
    fault_scenarios: dict[
        Literal[
            "provider",
            "java_gateway",
            "redis",
            "postgresql",
            "elasticsearch",
            "kafka",
            "instance_kill",
        ],
        FaultEvidence,
    ]
    rollback_drills: dict[
        Literal["application", "database", "knowledge_index", "backup_restore"],
        RollbackEvidence,
    ]
    safety_counters: SafetyCounters
    carryover_evidence_sha256: dict[Literal["phase3", "phase4", "phase5", "phase6"], str]
    approvals: dict[Literal["security", "sre", "business", "transaction"], ApprovalEvidence]
    canary_percentages: tuple[int, ...]
    automatic_rollback_enabled: bool

    @model_validator(mode="after")
    def validate_structure(self) -> Phase7ReleaseManifest:
        if self.window_end <= self.window_start:
            raise ValueError("phase 7 measurement window must end after it starts")
        if self.window_end - self.window_start > timedelta(days=7):
            raise ValueError("phase 7 measurement window must not exceed seven days")
        if not self.p50_turn_latency_ms <= self.p95_turn_latency_ms <= self.p99_turn_latency_ms:
            raise ValueError("phase 7 latency percentiles must be monotonic")
        if set(self.fault_scenarios) != _FAULT_NAMES:
            raise ValueError("phase 7 evidence must cover every required fault scenario")
        if set(self.rollback_drills) != _ROLLBACK_NAMES:
            raise ValueError("phase 7 evidence must cover every required rollback drill")
        if set(self.approvals) != _APPROVAL_ROLES:
            raise ValueError("phase 7 evidence must contain all required approvals")
        if set(self.carryover_evidence_sha256) != _CARRYOVER_PHASES or any(
            _SHA256.fullmatch(value) is None for value in self.carryover_evidence_sha256.values()
        ):
            raise ValueError("phase 3-6 carryover evidence hashes are incomplete")
        if self.canary_percentages != (1, 5, 25, 50, 100):
            raise ValueError("phase 7 canary percentages must be 1, 5, 25, 50, 100")
        return self

    def release_failures(
        self,
        *,
        evidence_hashes: dict[str, str],
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        current = now or datetime.now(timezone.utc)
        failures: list[str] = []
        if self.window_end - self.window_start < timedelta(minutes=30):
            failures.append("LOAD_WINDOW_TOO_SHORT")
        if self.window_end > current:
            failures.append("MEASUREMENT_FROM_FUTURE")
        if self.request_count < 1000:
            failures.append("INSUFFICIENT_REQUEST_COUNT")
        if self.success_rate < 0.995:
            failures.append("SUCCESS_RATE_BELOW_SLO")
        if self.throughput_rps < 1:
            failures.append("THROUGHPUT_NOT_MEASURED")
        if self.p95_turn_latency_ms > 5000:
            failures.append("P95_ABOVE_SLO")
        if self.trace_sample_count < 100 or self.trace_continuity_rate != 1:
            failures.append("TRACE_EVIDENCE_INCOMPLETE")
        supply = self.supply_chain
        if not all(
            (
                supply.sbom_generated,
                supply.provenance_attested,
                supply.immutable_base_images,
                supply.action_refs_pinned,
            )
        ) or any(
            (
                supply.critical_vulnerabilities,
                supply.high_vulnerabilities,
                supply.secrets_detected,
                supply.iac_critical_findings,
                supply.iac_high_findings,
            )
        ):
            failures.append("SUPPLY_CHAIN_GATE_FAILED")
        if any(
            not scenario.passed
            or scenario.recovery_time_seconds > 300
            or scenario.duplicate_side_effects != 0
            or scenario.blind_write_retries != 0
            or scenario.cross_tenant_successes != 0
            for scenario in self.fault_scenarios.values()
        ):
            failures.append("FAULT_INJECTION_GATE_FAILED")
        if any(
            not drill.passed or drill.data_loss_events != 0
            for drill in self.rollback_drills.values()
        ):
            failures.append("ROLLBACK_GATE_FAILED")
        if any(value != 0 for value in self.safety_counters.model_dump().values()):
            failures.append("SAFETY_RED_LINE_FAILED")
        if any(
            not approval.approved
            or approval.approved_at < self.window_end
            or approval.approved_at > current
            for approval in self.approvals.values()
        ):
            failures.append("APPROVAL_GATE_FAILED")
        if not self.automatic_rollback_enabled:
            failures.append("AUTOMATIC_ROLLBACK_DISABLED")
        if set(evidence_hashes) != _EVIDENCE_NAMES or any(
            _SHA256.fullmatch(value) is None for value in evidence_hashes.values()
        ):
            failures.append("EVIDENCE_HASHES_INCOMPLETE")
        elif any(
            evidence_hashes[phase] != expected
            for phase, expected in self.carryover_evidence_sha256.items()
        ):
            failures.append("CARRYOVER_EVIDENCE_MISMATCH")
        return tuple(sorted(failures))

    def attestation_payload(
        self,
        *,
        evidence_hashes: dict[str, str],
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        failures = self.release_failures(evidence_hashes=evidence_hashes, now=current)
        return {
            "schemaVersion": "damai.phase7.release-attestation/v1",
            "generatedAt": _utc(current),
            "environmentId": self.environment_id,
            "environmentTier": self.environment_tier,
            "gitCommit": self.git_commit,
            "imageRepository": self.image_repository,
            "imageDigest": self.image_digest,
            "configurationSha256": self.configuration_sha256,
            "windowStart": _utc(self.window_start),
            "windowEnd": _utc(self.window_end),
            "requestCount": self.request_count,
            "peakConcurrency": self.peak_concurrency,
            "successRate": self.success_rate,
            "throughputRps": self.throughput_rps,
            "p50TurnLatencyMs": self.p50_turn_latency_ms,
            "p95TurnLatencyMs": self.p95_turn_latency_ms,
            "p99TurnLatencyMs": self.p99_turn_latency_ms,
            "traceSampleCount": self.trace_sample_count,
            "traceContinuityRate": self.trace_continuity_rate,
            "supplyChain": self.supply_chain.model_dump(mode="json"),
            "faultScenarios": {
                name: scenario.model_dump(mode="json")
                for name, scenario in sorted(self.fault_scenarios.items())
            },
            "rollbackDrills": {
                name: drill.model_dump(mode="json")
                for name, drill in sorted(self.rollback_drills.items())
            },
            "safetyCounters": self.safety_counters.model_dump(mode="json"),
            "carryoverEvidenceSha256": dict(sorted(self.carryover_evidence_sha256.items())),
            "approvals": {
                role: {
                    "approved": approval.approved,
                    "approverId": approval.approver_id,
                    "approvalReference": approval.approval_reference,
                    "approvedAt": _utc(approval.approved_at),
                }
                for role, approval in sorted(self.approvals.items())
            },
            "canaryPercentages": list(self.canary_percentages),
            "automaticRollbackEnabled": self.automatic_rollback_enabled,
            "evidenceSha256": dict(sorted(evidence_hashes.items())),
            "failures": list(failures),
            "passed": not failures,
        }


def verify_phase7_attestation(
    path: Path,
    *,
    expected_commit: str,
    expected_image_digest: str,
    evidence_paths: Mapping[str, Path],
    max_age_hours: int = 168,
    now: datetime | None = None,
) -> str:
    if _COMMIT.fullmatch(expected_commit) is None:
        raise ValueError("expected phase 7 commit must be a full lowercase SHA")
    if _IMAGE_DIGEST.fullmatch(expected_image_digest) is None:
        raise ValueError("expected phase 7 image digest is invalid")
    if not 1 <= max_age_hours <= 168:
        raise ValueError("phase 7 evidence maximum age must be between 1 and 168 hours")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError("a bounded phase 7 attestation is required")
    raw = resolved.read_bytes()
    if not 0 < len(raw) <= _MAX_REPORT_BYTES:
        raise ValueError("a bounded phase 7 attestation is required")
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("phase 7 attestation must be valid UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("phase 7 attestation must be a JSON object")
    current = now or datetime.now(timezone.utc)
    actual_evidence_hashes = hash_evidence_files(evidence_paths)
    generated = _timestamp(report, "generatedAt")
    manifest = _manifest_from_attestation(report)
    canonical = manifest.attestation_payload(
        evidence_hashes=actual_evidence_hashes,
        now=generated,
    )
    if (
        _canonical_json(report) != _canonical_json(canonical)
        or manifest.git_commit != expected_commit
        or manifest.image_digest != expected_image_digest
        or canonical["passed"] is not True
    ):
        raise ValueError("phase 7 release attestation failed verification")
    if generated > current + timedelta(minutes=5) or generated < current - timedelta(
        hours=max_age_hours
    ):
        raise ValueError("phase 7 release attestation is stale")
    return hashlib.sha256(raw).hexdigest()


def _manifest_from_attestation(report: dict[str, object]) -> Phase7ReleaseManifest:
    if report.get("schemaVersion") != "damai.phase7.release-attestation/v1":
        raise ValueError("phase 7 attestation schema is invalid")
    approvals = report.get("approvals")
    if not isinstance(approvals, dict):
        raise ValueError("phase 7 approvals are invalid")
    normalized_approvals: dict[str, object] = {}
    for role, approval in approvals.items():
        if not isinstance(role, str) or not isinstance(approval, dict):
            raise ValueError("phase 7 approvals are invalid")
        normalized_approvals[role] = {
            "approved": approval.get("approved"),
            "approver_id": approval.get("approverId"),
            "approval_reference": approval.get("approvalReference"),
            "approved_at": approval.get("approvedAt"),
        }
    payload = {
        "schema_version": "damai.phase7.release/v1",
        "environment_id": report.get("environmentId"),
        "environment_tier": report.get("environmentTier"),
        "git_commit": report.get("gitCommit"),
        "image_repository": report.get("imageRepository"),
        "image_digest": report.get("imageDigest"),
        "configuration_sha256": report.get("configurationSha256"),
        "window_start": report.get("windowStart"),
        "window_end": report.get("windowEnd"),
        "request_count": report.get("requestCount"),
        "peak_concurrency": report.get("peakConcurrency"),
        "success_rate": report.get("successRate"),
        "throughput_rps": report.get("throughputRps"),
        "p50_turn_latency_ms": report.get("p50TurnLatencyMs"),
        "p95_turn_latency_ms": report.get("p95TurnLatencyMs"),
        "p99_turn_latency_ms": report.get("p99TurnLatencyMs"),
        "trace_sample_count": report.get("traceSampleCount"),
        "trace_continuity_rate": report.get("traceContinuityRate"),
        "supply_chain": report.get("supplyChain"),
        "fault_scenarios": report.get("faultScenarios"),
        "rollback_drills": report.get("rollbackDrills"),
        "safety_counters": report.get("safetyCounters"),
        "carryover_evidence_sha256": report.get("carryoverEvidenceSha256"),
        "approvals": normalized_approvals,
        "canary_percentages": report.get("canaryPercentages"),
        "automatic_rollback_enabled": report.get("automaticRollbackEnabled"),
    }
    try:
        return Phase7ReleaseManifest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("phase 7 attestation fields are invalid") from exc


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_evidence_files(
    evidence_paths: Mapping[str, Path],
    *,
    maximum_bytes: int = 20 * 1024 * 1024,
) -> dict[str, str]:
    if set(evidence_paths) != _EVIDENCE_NAMES:
        raise ValueError("every phase 7 evidence file is required")
    if not 1 <= maximum_bytes <= 100 * 1024 * 1024:
        raise ValueError("phase 7 evidence size limit is invalid")
    hashes: dict[str, str] = {}
    for name in sorted(_EVIDENCE_NAMES):
        resolved = evidence_paths[name].resolve()
        if not resolved.is_file():
            raise ValueError(f"{name} evidence must be a bounded nonempty file")
        raw = resolved.read_bytes()
        if not 0 < len(raw) <= maximum_bytes:
            raise ValueError(f"{name} evidence must be a bounded nonempty file")
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(payload: dict[str, object], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"phase 7 timestamp {key} is missing")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"phase 7 timestamp {key} is invalid") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"phase 7 timestamp {key} must include a timezone")
    return timestamp
