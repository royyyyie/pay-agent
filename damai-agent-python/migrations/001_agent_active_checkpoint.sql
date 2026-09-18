-- Apply with a dedicated migration role. Payload may contain user and Tool data.
CREATE TABLE IF NOT EXISTS agent_active_checkpoint (
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    session_key text NOT NULL CHECK (session_key <> ''),
    turn_id text NOT NULL CHECK (turn_id <> ''),
    version bigint NOT NULL CHECK (version >= 0),
    schema_version smallint NOT NULL CHECK (schema_version = 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, session_key)
);
