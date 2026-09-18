-- Apply after 002. Events can contain user text and Tool observations.
CREATE TABLE IF NOT EXISTS agent_turn_event (
    tenant_id text NOT NULL,
    session_key text NOT NULL,
    turn_id text NOT NULL,
    event_seq bigint NOT NULL CHECK (event_seq >= 1),
    schema_version smallint NOT NULL CHECK (schema_version = 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, session_key, turn_id, event_seq),
    FOREIGN KEY (tenant_id, session_key, turn_id)
        REFERENCES agent_turn (tenant_id, session_key, turn_id)
);

CREATE INDEX IF NOT EXISTS agent_turn_event_created_idx
    ON agent_turn_event (created_at);
