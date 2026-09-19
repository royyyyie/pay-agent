-- Phase 5: persistent Agent ticket-watch control plane.
-- Apply with a schema-change account before enabling watch:* scopes.
-- The program_id distribution matches shardingsphere-program-pro.yaml.

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_rule_0 (
    id BIGINT NOT NULL,
    tenant_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    idempotency_key VARCHAR(200) NOT NULL,
    program_id BIGINT NOT NULL,
    name VARCHAR(100) NOT NULL,
    ticket_category_ids VARCHAR(512) NULL,
    max_price DECIMAL(11,2) NULL,
    min_remaining BIGINT NOT NULL,
    check_interval_seconds INT NOT NULL,
    notification_channel VARCHAR(32) NOT NULL,
    rule_state VARCHAR(16) NOT NULL,
    next_check_time DATETIME(3) NOT NULL,
    last_checked_time DATETIME(3) NULL,
    last_triggered_time DATETIME(3) NULL,
    version BIGINT NOT NULL,
    create_time DATETIME(3) NOT NULL,
    edit_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_watch_idempotency
        (tenant_id, user_id, program_id, idempotency_key),
    KEY idx_agent_watch_owner_state (tenant_id, user_id, rule_state, edit_time),
    KEY idx_agent_watch_due (rule_state, next_check_time, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent ticket watch rules';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_rule_1
    LIKE damai_program_0.d_agent_watch_rule_0;

CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_rule_0
    LIKE damai_program_0.d_agent_watch_rule_0;

CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_rule_1
    LIKE damai_program_0.d_agent_watch_rule_0;
