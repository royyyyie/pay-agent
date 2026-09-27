package com.damai.controller.agent.purchase;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;

import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

/** Fails startup when confirmation authority is weak or shared with Agent delegation. */
@Configuration
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = "enabled",
        havingValue = "true")
public class PurchaseIntentSecurityConfiguration {

    public PurchaseIntentSecurityConfiguration(
            @Value("${AGENT_CONFIRMATION_HMAC_KEY:}") String confirmationKey,
            @Value("${AGENT_DELEGATION_HMAC_KEY:}") String delegationKey) {
        if (confirmationKey == null || confirmationKey.length() < 32) {
            throw new IllegalStateException(
                    "AGENT_CONFIRMATION_HMAC_KEY must contain at least 32 characters");
        }
        if (delegationKey != null
                && !delegationKey.isEmpty()
                && MessageDigest.isEqual(
                        confirmationKey.getBytes(StandardCharsets.UTF_8),
                        delegationKey.getBytes(StandardCharsets.UTF_8))) {
            throw new IllegalStateException(
                    "confirmation and delegation HMAC keys must be different");
        }
    }
}
