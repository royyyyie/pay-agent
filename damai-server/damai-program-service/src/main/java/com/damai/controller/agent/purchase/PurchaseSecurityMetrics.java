package com.damai.controller.agent.purchase;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Low-cardinality security and rejection metrics for the purchase boundary. */
@Component
public class PurchaseSecurityMetrics {

    private final MeterRegistry registry;
    private final Map<String, Counter> counters = new ConcurrentHashMap<>();

    public PurchaseSecurityMetrics(MeterRegistry registry) {
        this.registry = registry;
    }

    public void confirmationRejected(String reason) {
        increment("damai.agent.purchase.confirmation.rejections", reason);
    }

    public void grantReplay(String result) {
        increment("damai.agent.purchase.grant.replays", result);
    }

    public void submissionRejected(String reason) {
        increment("damai.agent.purchase.submission.rejections", reason);
    }

    private void increment(String name, String outcome) {
        String safeOutcome = switch (outcome) {
            case "business", "validation", "unexpected", "idempotent", "nonce" -> outcome;
            default -> "other";
        };
        counters.computeIfAbsent(
                        name + ':' + safeOutcome,
                        ignored -> Counter.builder(name)
                                .tag("reason", safeOutcome)
                                .register(registry))
                .increment();
    }
}
