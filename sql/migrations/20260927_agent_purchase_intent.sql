-- Phase 6 batch 1: Java-authoritative purchase intents and server-side confirmation grants.
-- Apply while AGENT_PURCHASE_INTENTS_ENABLED remains false.

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_purchase_intent_0 (
    id BIGINT NOT NULL,
    tenant_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    session_key VARCHAR(200) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    program_id BIGINT NOT NULL,
    ticket_category_id BIGINT NOT NULL,
    quantity INT NOT NULL,
    unit_amount_fen BIGINT NOT NULL,
    total_amount_fen BIGINT NOT NULL,
    currency CHAR(3) NOT NULL,
    quote_hash CHAR(64) NOT NULL,
    quote_expires_at DATETIME(3) NOT NULL,
    intent_state VARCHAR(32) NOT NULL,
    version BIGINT NOT NULL,
    confirmed_at DATETIME(3) NULL,
    submitted_at DATETIME(3) NULL,
    create_time DATETIME(3) NOT NULL,
    edit_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_purchase_intent_idempotency
        (tenant_id, user_id, idempotency_key),
    UNIQUE KEY uk_agent_purchase_intent_quote (quote_hash),
    KEY idx_agent_purchase_intent_owner
        (tenant_id, user_id, intent_state, create_time),
    KEY idx_agent_purchase_intent_quote_expiry
        (intent_state, quote_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent purchase intent and immutable quote snapshot';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_purchase_intent_1
    LIKE damai_program_0.d_agent_purchase_intent_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_purchase_intent_0
    LIKE damai_program_0.d_agent_purchase_intent_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_purchase_intent_1
    LIKE damai_program_0.d_agent_purchase_intent_0;

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_confirmation_grant_0 (
    id BIGINT NOT NULL,
    intent_id BIGINT NOT NULL,
    program_id BIGINT NOT NULL,
    intent_version BIGINT NOT NULL,
    tenant_id VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    session_key VARCHAR(200) NOT NULL,
    quote_hash CHAR(64) NOT NULL,
    proof_hash CHAR(64) NOT NULL,
    nonce_hash CHAR(64) NOT NULL,
    grant_state VARCHAR(16) NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    consumed_at DATETIME(3) NULL,
    create_time DATETIME(3) NOT NULL,
    edit_time DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_confirmation_grant_intent (intent_id),
    UNIQUE KEY uk_agent_confirmation_grant_proof (proof_hash),
    UNIQUE KEY uk_agent_confirmation_grant_nonce (nonce_hash),
    KEY idx_agent_confirmation_grant_available
        (grant_state, expires_at, intent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Server-side one-time Agent confirmation grants';

CREATE TABLE IF NOT EXISTS damai_program_0.d_agent_confirmation_grant_1
    LIKE damai_program_0.d_agent_confirmation_grant_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_confirmation_grant_0
    LIKE damai_program_0.d_agent_confirmation_grant_0;
CREATE TABLE IF NOT EXISTS damai_program_1.d_agent_confirmation_grant_1
    LIKE damai_program_0.d_agent_confirmation_grant_0;
