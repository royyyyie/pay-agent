-- Phase 6 completion: attendee-bound purchase intents and recoverable order submission.
-- Apply after 20260927_agent_purchase_intent.sql while both order-submission flags remain false.

ALTER TABLE damai_program_0.d_agent_purchase_intent_0
    ADD COLUMN ticket_user_refs VARCHAR(512) NULL AFTER quantity,
    ADD COLUMN order_number BIGINT NULL AFTER submitted_at,
    ADD KEY idx_agent_purchase_intent_order (order_number);
ALTER TABLE damai_program_0.d_agent_purchase_intent_1
    ADD COLUMN ticket_user_refs VARCHAR(512) NULL AFTER quantity,
    ADD COLUMN order_number BIGINT NULL AFTER submitted_at,
    ADD KEY idx_agent_purchase_intent_order (order_number);
ALTER TABLE damai_program_1.d_agent_purchase_intent_0
    ADD COLUMN ticket_user_refs VARCHAR(512) NULL AFTER quantity,
    ADD COLUMN order_number BIGINT NULL AFTER submitted_at,
    ADD KEY idx_agent_purchase_intent_order (order_number);
ALTER TABLE damai_program_1.d_agent_purchase_intent_1
    ADD COLUMN ticket_user_refs VARCHAR(512) NULL AFTER quantity,
    ADD COLUMN order_number BIGINT NULL AFTER submitted_at,
    ADD KEY idx_agent_purchase_intent_order (order_number);

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_order_submission_0 (
    id BIGINT NOT NULL,
    intent_id BIGINT NOT NULL,
    program_id BIGINT NOT NULL,
    intent_version BIGINT NOT NULL,
    tenant_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    session_key VARCHAR(200) NOT NULL,
    ticket_category_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    ticket_user_refs VARCHAR(512) NOT NULL,
    unit_amount_fen BIGINT NOT NULL,
    total_amount_fen BIGINT NOT NULL,
    currency CHAR(3) NOT NULL,
    order_number BIGINT NOT NULL,
    submission_state VARCHAR(32) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_time DATETIME(3) NOT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(64) NULL,
    lease_expires_at DATETIME(3) NULL,
    last_error_code VARCHAR(64) NULL,
    submitted_at DATETIME(3) NULL,
    create_time DATETIME(3) NOT NULL,
    edit_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_order_submission_intent (intent_id),
    UNIQUE KEY uk_agent_order_submission_order (order_number),
    KEY idx_agent_order_submission_due
        (submission_state, next_attempt_time, lease_expires_at),
    KEY idx_agent_order_submission_owner
        (tenant_id, user_id, create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Recoverable Agent order submission command and fact';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_order_submission_1
    LIKE damai_program_0.d_agent_order_submission_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_order_submission_0
    LIKE damai_program_0.d_agent_order_submission_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_order_submission_1
    LIKE damai_program_0.d_agent_order_submission_0;
