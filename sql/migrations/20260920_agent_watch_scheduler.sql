-- Phase 5 batch 2: scheduler leases and append-only watch execution history.
-- Apply only after 20260919_agent_watch_rule.sql and before enabling the scheduler.

ALTER TABLE damai_program_0.d_agent_watch_rule_0
    ADD COLUMN lease_owner VARCHAR(128) NULL AFTER last_triggered_time,
    ADD COLUMN lease_token VARCHAR(64) NULL AFTER lease_owner,
    ADD COLUMN lease_expires_at DATETIME(3) NULL AFTER lease_token,
    ADD COLUMN claimed_version BIGINT NULL AFTER lease_expires_at,
    ADD KEY idx_agent_watch_lease (lease_expires_at, lease_owner);

ALTER TABLE damai_program_0.d_agent_watch_rule_1
    ADD COLUMN lease_owner VARCHAR(128) NULL AFTER last_triggered_time,
    ADD COLUMN lease_token VARCHAR(64) NULL AFTER lease_owner,
    ADD COLUMN lease_expires_at DATETIME(3) NULL AFTER lease_token,
    ADD COLUMN claimed_version BIGINT NULL AFTER lease_expires_at,
    ADD KEY idx_agent_watch_lease (lease_expires_at, lease_owner);

ALTER TABLE damai_program_1.d_agent_watch_rule_0
    ADD COLUMN lease_owner VARCHAR(128) NULL AFTER last_triggered_time,
    ADD COLUMN lease_token VARCHAR(64) NULL AFTER lease_owner,
    ADD COLUMN lease_expires_at DATETIME(3) NULL AFTER lease_token,
    ADD COLUMN claimed_version BIGINT NULL AFTER lease_expires_at,
    ADD KEY idx_agent_watch_lease (lease_expires_at, lease_owner);

ALTER TABLE damai_program_1.d_agent_watch_rule_1
    ADD COLUMN lease_owner VARCHAR(128) NULL AFTER last_triggered_time,
    ADD COLUMN lease_token VARCHAR(64) NULL AFTER lease_owner,
    ADD COLUMN lease_expires_at DATETIME(3) NULL AFTER lease_token,
    ADD COLUMN claimed_version BIGINT NULL AFTER lease_expires_at,
    ADD KEY idx_agent_watch_lease (lease_expires_at, lease_owner);

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_execution_0 (
    id BIGINT NOT NULL,
    rule_id BIGINT NOT NULL,
    program_id BIGINT NOT NULL,
    rule_version BIGINT NOT NULL,
    lease_token VARCHAR(64) NOT NULL,
    checked_at DATETIME(3) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    matched_ticket_category_ids VARCHAR(512) NULL,
    matched_category_count INT NOT NULL,
    matched_remaining BIGINT NOT NULL,
    minimum_price DECIMAL(11,2) NULL,
    error_code VARCHAR(64) NULL,
    next_check_time DATETIME(3) NULL,
    create_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_watch_execution_attempt (rule_id, rule_version, lease_token),
    KEY idx_agent_watch_execution_rule (rule_id, checked_at),
    KEY idx_agent_watch_execution_program (program_id, checked_at),
    KEY idx_agent_watch_execution_outcome (outcome, checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent ticket watch execution history';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_execution_1
    LIKE damai_program_0.d_agent_watch_execution_0;

CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_execution_0
    LIKE damai_program_0.d_agent_watch_execution_0;

CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_execution_1
    LIKE damai_program_0.d_agent_watch_execution_0;
