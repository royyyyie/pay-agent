package com.damai.controller.agent.purchase;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;

/** Prevents an ORDER_WRITE deployment from bypassing the confirmation feature gate. */
@Configuration
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = "order-submission-enabled",
        havingValue = "true")
public class PurchaseOrderSecurityConfiguration {

    public PurchaseOrderSecurityConfiguration(
            @Value("${agent.purchase-intents.enabled:false}") boolean purchaseIntentsEnabled,
            @Value("${AGENT_DELEGATION_HMAC_KEY:}") String delegationKey,
            @Value("${AGENT_ORDER_FACT_API_KEY:}") String orderFactKey) {
        if (!purchaseIntentsEnabled) {
            throw new IllegalStateException(
                    "order submission requires agent.purchase-intents.enabled=true");
        }
        if (delegationKey == null || delegationKey.length() < 32) {
            throw new IllegalStateException(
                    "ORDER_WRITE requires AGENT_DELEGATION_HMAC_KEY with at least 32 characters");
        }
        if (orderFactKey == null || orderFactKey.length() < 32) {
            throw new IllegalStateException(
                    "ORDER_WRITE requires AGENT_ORDER_FACT_API_KEY with at least 32 characters");
        }
    }
}
