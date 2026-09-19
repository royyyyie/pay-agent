-- Append-only, metadata-only Tool audit. Grant runtime INSERT and SELECT, never UPDATE/DELETE.
CREATE TABLE IF NOT EXISTS agent_tool_audit (
    tenant_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    record_sha256 CHAR(64) NOT NULL,
    metadata JSONB NOT NULL,
    PRIMARY KEY (tenant_id, session_key, turn_id, tool_call_id)
);

CREATE INDEX IF NOT EXISTS agent_tool_audit_occurred_at_idx
    ON agent_tool_audit (occurred_at);
