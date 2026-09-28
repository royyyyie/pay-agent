package com.damai.controller.agent.purchase;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.util.Date;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/** Multi-replica recovery worker with database leases and fencing tokens. */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = {"enabled", "order-submission-enabled"},
        havingValue = "true")
public class PurchaseOrderRecoveryScheduler {

    private final PurchaseOrderSubmissionStore store;
    private final PurchaseOrderSubmissionProcessor processor;
    private final Clock clock;
    private final String owner;
    private final Duration leaseDuration;
    private final int partitions;
    private final int batchSize;
    private final AtomicLong backlog = new AtomicLong();

    public PurchaseOrderRecoveryScheduler(
            PurchaseOrderSubmissionStore store,
            PurchaseOrderSubmissionProcessor processor,
            MeterRegistry registry,
            @Value("${agent.purchase-intents.order-worker-id:${HOSTNAME:local}}") String owner,
            @Value("${agent.purchase-intents.order-lease-seconds:30}") long leaseSeconds,
            @Value("${agent.purchase-intents.order-scan-partitions:4}") int partitions,
            @Value("${agent.purchase-intents.order-batch-size:20}") int batchSize) {
        this.store = store;
        this.processor = processor;
        this.clock = Clock.systemUTC();
        this.owner = normalizeOwner(owner);
        this.leaseDuration = Duration.ofSeconds(Math.max(15, leaseSeconds));
        this.partitions = Math.max(1, partitions);
        this.batchSize = Math.min(100, Math.max(1, batchSize));
        Gauge.builder("damai.agent.purchase.submission.backlog", backlog, AtomicLong::get)
                .register(registry);
    }

    @Scheduled(fixedDelayString = "${agent.purchase-intents.order-worker-delay-ms:1000}")
    public void scheduledRun() {
        try {
            runOnce();
        } catch (RuntimeException exception) {
            log.error("Agent order recovery cycle failed", exception);
        }
    }

    public int runOnce() {
        Date now = Date.from(clock.instant());
        backlog.set(Math.max(0, store.countDue(now)));
        List<PurchaseOrderSubmission> claimed = store.claimDue(
                now, owner, leaseDuration, partitions, batchSize);
        claimed.forEach(processor::process);
        return claimed.size();
    }

    private static String normalizeOwner(String value) {
        String normalized = value == null || value.isBlank() ? "local" : value.trim();
        return normalized.length() <= 128 ? normalized : normalized.substring(0, 128);
    }
}
