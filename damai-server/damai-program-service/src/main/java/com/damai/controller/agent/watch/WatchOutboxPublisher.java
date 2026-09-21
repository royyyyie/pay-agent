package com.damai.controller.agent.watch;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.util.Date;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/** At-least-once Kafka publisher; consumer event IDs absorb ambiguous send acknowledgements. */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = {"enabled", "notification-enabled"},
        havingValue = "true")
public class WatchOutboxPublisher {

    private final WatchOutboxService outboxService;
    private final WatchEventTransport transport;
    private final Clock clock;
    private final String owner;
    private final Duration leaseDuration;
    private final Duration initialBackoff;
    private final int maximumAttempts;
    private final int scanPartitions;
    private final int batchSize;
    private final AtomicLong backlog = new AtomicLong();
    private final Counter published;
    private final Counter retried;
    private final Counter dead;
    private final Counter stale;

    @Autowired
    public WatchOutboxPublisher(
            WatchOutboxService outboxService,
            WatchEventTransport transport,
            MeterRegistry registry,
            @Value("${agent.watch-rules.notification-publisher-id:${HOSTNAME:local}}") String owner,
            @Value("${agent.watch-rules.notification-outbox-lease-seconds:30}") long leaseSeconds,
            @Value("${agent.watch-rules.notification-retry-seconds:5}") long retrySeconds,
            @Value("${agent.watch-rules.notification-maximum-attempts:8}") int maximumAttempts,
            @Value("${agent.watch-rules.scheduler-scan-partitions:4}") int scanPartitions,
            @Value("${agent.watch-rules.notification-batch-size:50}") int batchSize) {
        this(
                outboxService,
                transport,
                registry,
                Clock.systemUTC(),
                owner,
                leaseSeconds,
                retrySeconds,
                maximumAttempts,
                scanPartitions,
                batchSize);
    }

    public WatchOutboxPublisher(
            WatchOutboxService outboxService,
            WatchEventTransport transport,
            MeterRegistry registry,
            Clock clock,
            String owner,
            long leaseSeconds,
            long retrySeconds,
            int maximumAttempts,
            int scanPartitions,
            int batchSize) {
        this.outboxService = outboxService;
        this.transport = transport;
        this.clock = clock;
        this.owner = normalizeOwner(owner);
        this.leaseDuration = Duration.ofSeconds(Math.max(5, leaseSeconds));
        this.initialBackoff = Duration.ofSeconds(Math.max(1, retrySeconds));
        this.maximumAttempts = Math.max(1, maximumAttempts);
        this.scanPartitions = Math.max(1, scanPartitions);
        this.batchSize = Math.min(100, Math.max(1, batchSize));
        Gauge.builder("damai.agent.watch.outbox.backlog", backlog, AtomicLong::get)
                .register(registry);
        this.published = registry.counter("damai.agent.watch.outbox.published");
        this.retried = registry.counter("damai.agent.watch.outbox.retried");
        this.dead = registry.counter("damai.agent.watch.outbox.dead");
        this.stale = registry.counter("damai.agent.watch.outbox.stale");
    }

    @Scheduled(fixedDelayString = "${agent.watch-rules.notification-publish-delay-ms:1000}")
    public void scheduledRun() {
        try {
            runOnce();
        } catch (RuntimeException exception) {
            log.error("Watch notification outbox cycle failed", exception);
        }
    }

    public int runOnce() {
        Date now = Date.from(clock.instant());
        backlog.set(Math.max(0, outboxService.countDue(now)));
        List<WatchNotificationOutbox> claimed = outboxService.claimDue(
                now, owner, leaseDuration, scanPartitions, batchSize);
        for (WatchNotificationOutbox outbox : claimed) {
            publishOne(outbox);
        }
        return claimed.size();
    }

    private void publishOne(WatchNotificationOutbox outbox) {
        try {
            WatchNotificationEvent event = WatchNotificationEvent.from(outbox);
            event.validate();
            transport.publish(event);
            if (outboxService.markPublished(outbox, Date.from(clock.instant()))) {
                published.increment();
            } else {
                stale.increment();
            }
        } catch (RuntimeException exception) {
            Date failedAt = Date.from(clock.instant());
            int nextAttempt = outbox.getAttemptCount() + 1;
            Date nextAttemptTime = Date.from(failedAt.toInstant().plus(backoff(nextAttempt)));
            if (outboxService.markFailed(
                    outbox, failedAt, nextAttemptTime, maximumAttempts)) {
                if (nextAttempt >= maximumAttempts) {
                    dead.increment();
                } else {
                    retried.increment();
                }
            } else {
                stale.increment();
            }
            log.warn(
                    "Watch notification publish failed, eventId={}, attempt={}, failureType={}",
                    outbox.getEventId(),
                    nextAttempt,
                    exception.getClass().getSimpleName());
        }
    }

    private Duration backoff(int attempt) {
        int shift = Math.min(10, Math.max(0, attempt - 1));
        long seconds = Math.min(3600, initialBackoff.getSeconds() * (1L << shift));
        return Duration.ofSeconds(seconds);
    }

    private static String normalizeOwner(String owner) {
        String value = owner == null || owner.isBlank() ? "local" : owner.trim();
        return value.length() <= 128 ? value : value.substring(0, 128);
    }
}
