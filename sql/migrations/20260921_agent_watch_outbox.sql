-- Phase 5 batch 3: transactional notification outbox and idempotent in-app inbox.
-- Apply after 20260920_agent_watch_scheduler.sql while notification publishing is disabled.

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_outbox_0 (
    id BIGINT NOT NULL,
    event_id VARCHAR(80) NOT NULL,
    rule_id BIGINT NOT NULL,
    program_id BIGINT NOT NULL,
    rule_version BIGINT NOT NULL,
    execution_id BIGINT NOT NULL,
    condition_fingerprint CHAR(64) NOT NULL,
    dedupe_window_start DATETIME(3) NOT NULL,
    dedupe_key CHAR(64) NOT NULL,
    channel VARCHAR(32) NOT NULL,
    matched_ticket_category_ids VARCHAR(512) NOT NULL,
    matched_category_count INT NOT NULL,
    matched_remaining BIGINT NOT NULL,
    minimum_price DECIMAL(11,2) NOT NULL,
    freshness_at DATETIME(3) NOT NULL,
    publish_state VARCHAR(16) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_time DATETIME(3) NOT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(64) NULL,
    lease_expires_at DATETIME(3) NULL,
    last_error_code VARCHAR(64) NULL,
    published_at DATETIME(3) NULL,
    create_time DATETIME(3) NOT NULL,
    edit_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_watch_outbox_event (event_id),
    UNIQUE KEY uk_agent_watch_outbox_dedupe (dedupe_key),
    KEY idx_agent_watch_outbox_due
        (publish_state, next_attempt_time, lease_expires_at, id),
    KEY idx_agent_watch_outbox_rule (rule_id, create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent watch notification transactional outbox';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_outbox_1
    LIKE damai_program_0.d_agent_watch_outbox_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_outbox_0
    LIKE damai_program_0.d_agent_watch_outbox_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_outbox_1
    LIKE damai_program_0.d_agent_watch_outbox_0;

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_notification_0 (
    id BIGINT NOT NULL,
    event_id VARCHAR(80) NOT NULL,
    rule_id BIGINT NOT NULL,
    program_id BIGINT NOT NULL,
    rule_version BIGINT NOT NULL,
    tenant_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    channel VARCHAR(32) NOT NULL,
    delivery_state VARCHAR(16) NOT NULL,
    title VARCHAR(200) NOT NULL,
    content VARCHAR(1000) NOT NULL,
    matched_ticket_category_ids VARCHAR(512) NOT NULL,
    matched_category_count INT NOT NULL,
    matched_remaining BIGINT NOT NULL,
    minimum_price DECIMAL(11,2) NOT NULL,
    freshness_at DATETIME(3) NOT NULL,
    create_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_watch_notification_event (event_id),
    KEY idx_agent_watch_notification_owner
        (tenant_id, user_id, delivery_state, create_time),
    KEY idx_agent_watch_notification_rule (rule_id, create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Idempotent in-app watch notifications';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_watch_notification_1
    LIKE damai_program_0.d_agent_watch_notification_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_notification_0
    LIKE damai_program_0.d_agent_watch_notification_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_watch_notification_1
    LIKE damai_program_0.d_agent_watch_notification_0;
