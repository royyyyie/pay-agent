package com.damai.controller.agent.purchase;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.util.Date;
import java.util.EnumMap;
import java.util.Map;
import java.util.Optional;

/** Query-before-write processor. A reclaimed unknown command is never blindly written again. */
@Slf4j
@Service
public class PurchaseOrderSubmissionProcessor {

    private final PurchaseOrderSubmissionStore store;
    private final PurchaseOrderGateway gateway;
    private final Clock clock;
    private final Duration initialBackoff;
    private final int maximumAttempts;
    private final Map<PurchaseSubmissionOutcome, Counter> outcomes;

    @Autowired
    public PurchaseOrderSubmissionProcessor(
            PurchaseOrderSubmissionStore store,
            PurchaseOrderGateway gateway,
            MeterRegistry meterRegistry,
            @Value("${agent.purchase-intents.order-retry-seconds:5}") long retrySeconds,
            @Value("${agent.purchase-intents.order-maximum-attempts:6}") int maximumAttempts) {
        this(
                store,
                gateway,
                meterRegistry,
                Clock.systemUTC(),
                retrySeconds,
                maximumAttempts);
    }

    PurchaseOrderSubmissionProcessor(
            PurchaseOrderSubmissionStore store,
            PurchaseOrderGateway gateway,
            MeterRegistry meterRegistry,
            Clock clock,
            long retrySeconds,
            int maximumAttempts) {
        this.store = store;
        this.gateway = gateway;
        this.clock = clock;
        this.initialBackoff = Duration.ofSeconds(Math.max(1, retrySeconds));
        this.maximumAttempts = Math.max(1, maximumAttempts);
        this.outcomes = new EnumMap<>(PurchaseSubmissionOutcome.class);
        for (PurchaseSubmissionOutcome outcome : PurchaseSubmissionOutcome.values()) {
            outcomes.put(
                    outcome,
                    Counter.builder("damai.agent.purchase.submission.outcomes")
                            .tag("outcome", outcome.name().toLowerCase())
                            .register(meterRegistry));
        }
    }

    public void process(PurchaseOrderSubmission submission) {
        Optional<PurchaseOrderFact> existing;
        try {
            existing = gateway.find(submission.getOrderNumber());
        } catch (PurchaseOrderGatewayException exception) {
            retryOrEscalate(submission, exception.code(), exception.retryable());
            return;
        }
        if (existing.isPresent()) {
            reconcile(submission, existing.get());
            return;
        }
        if (submission.isReclaimedUnknown()) {
            reconcileMissingOrEscalate(submission);
            return;
        }
        try {
            Long createdOrder = gateway.create(submission);
            if (!submission.getOrderNumber().equals(createdOrder)) {
                terminal(submission, true, "ORDER_NUMBER_MISMATCH");
                return;
            }
            if (store.markSubmitted(submission, Date.from(clock.instant()))) {
                outcomes.get(PurchaseSubmissionOutcome.SUBMITTED).increment();
            } else {
                outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
            }
        } catch (PurchaseOrderGatewayException exception) {
            if (exception.retryable()) {
                deferReconciliation(submission, exception.code());
            } else {
                terminal(submission, false, exception.code());
            }
        }
    }

    private void reconcile(PurchaseOrderSubmission submission, PurchaseOrderFact fact) {
        if (!matches(submission, fact)) {
            terminal(submission, true, "ORDER_FACT_BINDING_MISMATCH");
            return;
        }
        Date submittedAt = fact.createdAt() == null ? Date.from(clock.instant()) : fact.createdAt();
        if (store.markSubmitted(submission, submittedAt)) {
            outcomes.get(PurchaseSubmissionOutcome.RECONCILED).increment();
        } else {
            outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
        }
    }

    private boolean matches(PurchaseOrderSubmission submission, PurchaseOrderFact fact) {
        try {
            BigDecimal expectedPrice = BigDecimal.valueOf(submission.getTotalAmountFen(), 2);
            return submission.getOrderNumber().equals(fact.orderNumber())
                    && submission.getProgramId().equals(fact.programId())
                    && Long.parseLong(submission.getUserId()) == fact.userId()
                    && fact.orderPrice() != null
                    && expectedPrice.compareTo(fact.orderPrice()) == 0;
        } catch (RuntimeException exception) {
            return false;
        }
    }

    private void retryOrEscalate(
            PurchaseOrderSubmission submission, String errorCode, boolean retryable) {
        if (!retryable) {
            terminal(submission, false, errorCode);
            return;
        }
        if (submission.getAttemptCount() >= maximumAttempts) {
            terminal(submission, true, "RETRY_EXHAUSTED_" + errorCode);
            return;
        }
        Date failedAt = Date.from(clock.instant());
        Date nextAttempt = Date.from(failedAt.toInstant().plus(backoff(submission.getAttemptCount())));
        if (store.markRetry(submission, failedAt, nextAttempt, "SAFE_RETRY_" + errorCode)) {
            outcomes.get(PurchaseSubmissionOutcome.RETRY).increment();
        } else {
            outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
        }
        log.warn(
                "Agent order submission deferred, intentId={}, orderNumber={}, attempt={}, code={}",
                submission.getIntentId(),
                submission.getOrderNumber(),
                submission.getAttemptCount(),
                errorCode);
    }

    /** A failed create call may have committed remotely. It can only be queried, never replayed. */
    private void deferReconciliation(PurchaseOrderSubmission submission, String errorCode) {
        Date failedAt = Date.from(clock.instant());
        Date nextAttempt = Date.from(failedAt.toInstant().plus(initialBackoff));
        if (store.markReconcile(
                submission,
                failedAt,
                nextAttempt,
                "AMBIGUOUS_" + errorCode)) {
            outcomes.get(PurchaseSubmissionOutcome.RECONCILE).increment();
        } else {
            outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
        }
        log.warn(
                "Agent order create outcome unknown; reconciliation only, intentId={}, "
                        + "orderNumber={}, code={}",
                submission.getIntentId(),
                submission.getOrderNumber(),
                errorCode);
    }

    private void reconcileMissingOrEscalate(PurchaseOrderSubmission submission) {
        if (submission.getAttemptCount() >= maximumAttempts) {
            terminal(submission, true, "AMBIGUOUS_CREATE_WITHOUT_ORDER_FACT");
            return;
        }
        Date checkedAt = Date.from(clock.instant());
        Date nextAttempt = Date.from(
                checkedAt.toInstant().plus(backoff(submission.getAttemptCount())));
        if (store.markReconcile(
                submission,
                checkedAt,
                nextAttempt,
                "RECONCILE_ORDER_FACT_NOT_FOUND")) {
            outcomes.get(PurchaseSubmissionOutcome.RECONCILE).increment();
        } else {
            outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
        }
        log.warn(
                "Agent order fact not visible; reconciliation remains read-only, intentId={}, "
                        + "orderNumber={}, attempt={}",
                submission.getIntentId(),
                submission.getOrderNumber(),
                submission.getAttemptCount());
    }

    private void terminal(
            PurchaseOrderSubmission submission, boolean unknown, String errorCode) {
        if (store.markTerminal(
                submission, Date.from(clock.instant()), unknown, errorCode)) {
            outcomes.get(unknown
                    ? PurchaseSubmissionOutcome.MANUAL_REVIEW
                    : PurchaseSubmissionOutcome.FAILED).increment();
        } else {
            outcomes.get(PurchaseSubmissionOutcome.STALE).increment();
        }
        log.error(
                "Agent order submission terminal, intentId={}, orderNumber={}, unknown={}, code={}",
                submission.getIntentId(),
                submission.getOrderNumber(),
                unknown,
                errorCode);
    }

    private Duration backoff(int attempt) {
        int shift = Math.min(10, Math.max(0, attempt - 1));
        return Duration.ofSeconds(Math.min(3600, initialBackoff.getSeconds() * (1L << shift)));
    }
}
