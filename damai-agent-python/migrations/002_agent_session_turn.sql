-- Apply after 001. Run as a migration role; these rows may contain user content.
CREATE TABLE IF NOT EXISTS agent_session (
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    session_key text NOT NULL CHECK (session_key <> ''),
    fence_version bigint NOT NULL DEFAULT 0 CHECK (fence_version >= 0),
    active_turn_id text,
    next_message_seq bigint NOT NULL DEFAULT 1 CHECK (next_message_seq >= 1),
    PRIMARY KEY (tenant_id, session_key)
);

CREATE TABLE IF NOT EXISTS agent_turn (
    tenant_id text NOT NULL,
    session_key text NOT NULL,
    turn_id text NOT NULL CHECK (turn_id <> ''),
    idempotency_key text NOT NULL CHECK (idempotency_key <> ''),
    request_fingerprint char(64) NOT NULL,
    fence_version bigint NOT NULL CHECK (fence_version > 0),
    status text NOT NULL CHECK (status IN ('running', 'completed')),
    result_schema_version smallint,
    result_payload jsonb,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, session_key, turn_id),
    UNIQUE (tenant_id, session_key, idempotency_key),
    FOREIGN KEY (tenant_id, session_key)
        REFERENCES agent_session (tenant_id, session_key),
    CHECK (
        (status = 'running' AND result_payload IS NULL AND completed_at IS NULL)
        OR
        (status = 'completed' AND result_schema_version = 1
         AND jsonb_typeof(result_payload) = 'object' AND completed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS agent_message (
    tenant_id text NOT NULL,
    session_key text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 1),
    turn_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    schema_version smallint NOT NULL CHECK (schema_version = 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (tenant_id, session_key, sequence),
    UNIQUE (tenant_id, session_key, turn_id, ordinal),
    FOREIGN KEY (tenant_id, session_key, turn_id)
        REFERENCES agent_turn (tenant_id, session_key, turn_id)
);

CREATE INDEX IF NOT EXISTS agent_message_recent_turn_idx
    ON agent_message (tenant_id, session_key, sequence DESC);
