"""Short-lived HMAC delegation for the opt-in multi-tenant durable API."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import TicketTurnContext, ToolRisk


class DelegationError(ValueError):
    """A delegation is missing, malformed, expired, or unauthenticated."""


class DelegationClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tenantId: str = Field(min_length=1, max_length=200)
    userId: str = Field(min_length=1, max_length=200)
    sessionKey: str = Field(min_length=1, max_length=200)
    turnId: str = Field(min_length=1, max_length=200)
    requestId: str = Field(min_length=1, max_length=200)
    traceId: str = Field(min_length=1, max_length=200)
    locale: str = Field(min_length=1, max_length=32)
    channel: str = Field(min_length=1, max_length=100)
    toolScopes: list[str] = Field(max_length=32)
    riskCeiling: Literal["READ_ONLY", "REVERSIBLE_WRITE"]
    delegationTokenId: str = Field(min_length=1, max_length=200)
    issuedAt: int
    expiresAt: int


def verify_delegation(
    encoded: str, signature: str, secret: str, *, now: int | None = None
) -> TicketTurnContext:
    if (
        not secret
        or len(encoded) > 4096
        or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None
        or re.fullmatch(r"[0-9a-f]{64}", signature) is None
    ):
        raise DelegationError("invalid delegation")
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256)
    if not hmac.compare_digest(expected.hexdigest(), signature):
        raise DelegationError("invalid delegation")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        claims = DelegationClaims.model_validate(json.loads(payload))
    except (binascii.Error, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise DelegationError("invalid delegation") from exc
    instant = int(time.time()) if now is None else now
    if (
        claims.issuedAt > instant + 30
        or claims.expiresAt <= instant
        or claims.expiresAt - claims.issuedAt > 300
        or claims.expiresAt <= claims.issuedAt
        or any(not scope or len(scope) > 100 for scope in claims.toolScopes)
    ):
        raise DelegationError("delegation expired or invalid")
    return TicketTurnContext(
        tenant_id=claims.tenantId,
        user_id=claims.userId,
        session_key=claims.sessionKey,
        turn_id=claims.turnId,
        request_id=claims.requestId,
        trace_id=claims.traceId,
        locale=claims.locale,
        channel=claims.channel,
        tool_scopes=frozenset(claims.toolScopes),
        risk_ceiling=ToolRisk(claims.riskCeiling),
        delegation_token_id=claims.delegationTokenId,
        delegation_expires_at=claims.expiresAt,
        delegation_encoded=encoded,
        delegation_signature=signature,
    )
