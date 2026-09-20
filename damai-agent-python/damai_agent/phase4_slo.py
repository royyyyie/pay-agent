"""Governed evidence model for phase 4 end-to-end SLO and fault acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class FaultScenarioEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    passed: bool
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
    recovery_time_ms: int = Field(ge=0, le=3_600_000)
    duplicate_side_effects: int = Field(ge=0, le=1_000_000)


class Phase4SloManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["damai.phase4.slo/v1"]
    environment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    environment_tier: Literal["test", "staging"]
    deployment_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}$")
    knowledge_index_version: str = Field(pattern=r"^knowledge@sha256:[0-9a-f]{16}$")
    window_start: AwareDatetime
    window_end: AwareDatetime
    request_count: int = Field(ge=1, le=100_000_000)
    availability: float = Field(ge=0, le=1)
    p95_turn_latency_ms: float = Field(ge=0, le=3_600_000)
    trace_sample_count: int = Field(ge=0, le=1_000_000)
    trace_continuity_rate: float = Field(ge=0, le=1)
    rag_java_trace_sample_count: int = Field(ge=0, le=1_000_000)
    fault_scenarios: dict[
        Literal["provider", "java_gateway", "redis", "postgresql"], FaultScenarioEvidence
    ]
    rollback_drill_passed: bool
    operations_approved: bool
    security_approved: bool
    reviewer_count: int = Field(ge=2, le=20)
    approval_status: Literal["draft", "approved", "rejected"]
    approval_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
    approved_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window_and_scenarios(self) -> Phase4SloManifest:
        if self.window_end <= self.window_start:
            raise ValueError("SLO evidence window must end after it starts")
        if self.window_end - self.window_start > timedelta(days=7):
            raise ValueError("SLO evidence window must not exceed seven days")
        required = {"provider", "java_gateway", "redis", "postgresql"}
        if set(self.fault_scenarios) != required:
            raise ValueError("SLO evidence must cover all required fault scenarios")
        return self

    def release_eligible(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return (
            self.window_end - self.window_start >= timedelta(minutes=10)
            and self.window_end <= current
            and self.request_count >= 100
            and self.availability >= 0.99
            and self.p95_turn_latency_ms <= 10_000
            and self.trace_sample_count >= 20
            and self.trace_continuity_rate == 1.0
            and self.rag_java_trace_sample_count >= 10
            and all(
                scenario.passed and scenario.duplicate_side_effects == 0
                for scenario in self.fault_scenarios.values()
            )
            and self.rollback_drill_passed
            and self.operations_approved
            and self.security_approved
            and self.approval_status == "approved"
            and self.approved_at >= self.window_end
            and self.approved_at <= current
        )

    def attestation_payload(self, *, evidence_hashes: dict[str, str]) -> dict[str, object]:
        passed = self.release_eligible()
        return {
            "schemaVersion": "damai.phase4.slo-attestation/v1",
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "environmentId": self.environment_id,
            "environmentTier": self.environment_tier,
            "deploymentVersion": self.deployment_version,
            "knowledgeIndexVersion": self.knowledge_index_version,
            "windowStart": self.window_start.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "windowEnd": self.window_end.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "requestCount": self.request_count,
            "availability": self.availability,
            "p95TurnLatencyMs": self.p95_turn_latency_ms,
            "traceSampleCount": self.trace_sample_count,
            "traceContinuityRate": self.trace_continuity_rate,
            "ragJavaTraceSampleCount": self.rag_java_trace_sample_count,
            "faultScenarios": {
                name: {
                    "passed": scenario.passed,
                    "evidenceReference": scenario.evidence_reference,
                    "recoveryTimeMs": scenario.recovery_time_ms,
                    "duplicateSideEffects": scenario.duplicate_side_effects,
                }
                for name, scenario in sorted(self.fault_scenarios.items())
            },
            "rollbackDrillPassed": self.rollback_drill_passed,
            "operationsApproved": self.operations_approved,
            "securityApproved": self.security_approved,
            "reviewerCount": self.reviewer_count,
            "approvalReference": self.approval_reference,
            "approvedAt": self.approved_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "evidenceSha256": dict(sorted(evidence_hashes.items())),
            "passed": passed,
        }
