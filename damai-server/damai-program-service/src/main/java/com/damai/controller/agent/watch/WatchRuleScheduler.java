package com.damai.controller.agent.watch;

import com.damai.service.TicketCategoryService;
import com.damai.vo.TicketCategoryDetailVo;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.util.Date;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;

/** Multi-replica-safe live ticket watch scheduler. */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = {"enabled", "scheduler-enabled"},
        havingValue = "true")
public class WatchRuleScheduler {

    private final WatchRuleLeaseService leaseService;
    private final TicketCategoryService ticketCategoryService;
    private final Clock clock;
    private final String leaseOwner;
    private final Duration leaseDuration;
    private final int scanPartitions;
    private final int batchSize;
    private final AtomicLong backlog = new AtomicLong();
    private final Timer checkDelay;
    private final Map<WatchCheckOutcome, Counter> outcomeCounters;
    private final Counter inventoryErrors;
    private final Counter databaseErrors;

    @Autowired
    public WatchRuleScheduler(
            WatchRuleLeaseService leaseService,
            TicketCategoryService ticketCategoryService,
            MeterRegistry meterRegistry,
            @Value("${agent.watch-rules.scheduler-instance-id:${HOSTNAME:local}}")
                    String leaseOwner,
            @Value("${agent.watch-rules.scheduler-lease-seconds:60}") long leaseSeconds,
            @Value("${agent.watch-rules.scheduler-scan-partitions:4}") int scanPartitions,
            @Value("${agent.watch-rules.scheduler-batch-size:50}") int batchSize) {
        this(
                leaseService,
                ticketCategoryService,
                meterRegistry,
                Clock.systemUTC(),
                leaseOwner,
                leaseSeconds,
                scanPartitions,
                batchSize);
    }

    public WatchRuleScheduler(
            WatchRuleLeaseService leaseService,
            TicketCategoryService ticketCategoryService,
            MeterRegistry meterRegistry,
            Clock clock,
            String leaseOwner,
            long leaseSeconds,
            int scanPartitions,
            int batchSize) {
        this.leaseService = leaseService;
        this.ticketCategoryService = ticketCategoryService;
        this.clock = clock;
        this.leaseOwner = requireOwner(leaseOwner);
        this.leaseDuration = Duration.ofSeconds(Math.max(5, leaseSeconds));
        this.scanPartitions = Math.max(1, scanPartitions);
        this.batchSize = Math.min(100, Math.max(1, batchSize));
        Gauge.builder("damai.agent.watch.scheduler.backlog", backlog, AtomicLong::get)
                .description("Due watch rules not currently leased")
                .register(meterRegistry);
        this.checkDelay = Timer.builder("damai.agent.watch.scheduler.check.delay")
                .description("Delay between due time and live inventory evaluation")
                .register(meterRegistry);
        this.outcomeCounters = new EnumMap<>(WatchCheckOutcome.class);
        for (WatchCheckOutcome outcome : WatchCheckOutcome.values()) {
            outcomeCounters.put(
                    outcome,
                    Counter.builder("damai.agent.watch.scheduler.executions")
                            .tag("outcome", outcome.name().toLowerCase())
                            .register(meterRegistry));
        }
        this.inventoryErrors = dependencyCounter(meterRegistry, "ticket_inventory");
        this.databaseErrors = dependencyCounter(meterRegistry, "database");
    }

    @Scheduled(fixedDelayString = "${agent.watch-rules.scheduler-fixed-delay-ms:5000}")
    public void scheduledRun() {
        try {
            runOnce();
        } catch (RuntimeException exception) {
            databaseErrors.increment();
            log.error("Ticket watch scheduler cycle failed", exception);
        }
    }

    /** Public for deterministic contract tests and operational one-shot probes. */
    public int runOnce() {
        Date claimTime = Date.from(clock.instant());
        backlog.set(Math.max(0, leaseService.countDue(claimTime)));
        List<WatchRule> claimed = leaseService.claimDue(
                claimTime,
                leaseOwner,
                leaseDuration,
                scanPartitions,
                batchSize);
        if (claimed.isEmpty()) {
            return 0;
        }

        Map<Long, List<TicketCategoryDetailVo>> inventory;
        try {
            List<Long> programIds = claimed.stream()
                    .map(WatchRule::getProgramId)
                    .filter(Objects::nonNull)
                    .distinct()
                    .toList();
            inventory = ticketCategoryService.selectListByPrograms(programIds);
        } catch (RuntimeException exception) {
            inventoryErrors.increment();
            log.warn(
                    "Live ticket inventory read failed for {} claimed watch rules, failureType={}",
                    claimed.size(),
                    exception.getClass().getSimpleName());
            Date checkedAt = Date.from(clock.instant());
            for (WatchRule rule : claimed) {
                finish(
                        rule,
                        WatchEvaluation.dependencyError("TICKET_INVENTORY_UNAVAILABLE"),
                        checkedAt);
            }
            return claimed.size();
        }

        Date checkedAt = Date.from(clock.instant());
        for (WatchRule rule : claimed) {
            WatchEvaluation evaluation = WatchRuleEvaluator.evaluate(
                    rule, inventory.getOrDefault(rule.getProgramId(), List.of()));
            finish(rule, evaluation, checkedAt);
        }
        return claimed.size();
    }

    private void finish(WatchRule rule, WatchEvaluation evaluation, Date checkedAt) {
        recordDelay(rule, checkedAt);
        Date nextCheckTime = Date.from(checkedAt.toInstant().plusSeconds(
                Math.max(30, rule.getCheckIntervalSeconds() == null
                        ? 300
                        : rule.getCheckIntervalSeconds())));
        boolean accepted = leaseService.complete(rule, evaluation, checkedAt, nextCheckTime);
        if (accepted) {
            outcomeCounters.get(evaluation.outcome()).increment();
            return;
        }
        outcomeCounters.get(WatchCheckOutcome.STALE).increment();
        leaseService.recordStale(rule, checkedAt);
    }

    private void recordDelay(WatchRule rule, Date checkedAt) {
        if (rule.getNextCheckTime() == null || checkedAt.before(rule.getNextCheckTime())) {
            return;
        }
        checkDelay.record(Duration.between(
                rule.getNextCheckTime().toInstant(), checkedAt.toInstant()));
    }

    private static Counter dependencyCounter(MeterRegistry registry, String dependency) {
        return Counter.builder("damai.agent.watch.scheduler.dependency.errors")
                .tag("dependency", dependency)
                .register(registry);
    }

    private static String requireOwner(String owner) {
        if (owner == null || owner.isBlank()) {
            return "local";
        }
        String normalized = owner.trim();
        return normalized.length() <= 128 ? normalized : normalized.substring(0, 128);
    }
}
