package com.damai.controller.agent.watch;

import java.math.BigDecimal;

/** Sanitized result persisted for one watch execution. */
public record WatchEvaluation(
        WatchCheckOutcome outcome,
        String matchedTicketCategoryIds,
        int matchedCategoryCount,
        long matchedRemaining,
        BigDecimal minimumPrice,
        String errorCode) {

    public static WatchEvaluation dependencyError(String errorCode) {
        return new WatchEvaluation(
                WatchCheckOutcome.DEPENDENCY_ERROR, null, 0, 0, null, errorCode);
    }

    public static WatchEvaluation stale() {
        return new WatchEvaluation(
                WatchCheckOutcome.STALE, null, 0, 0, null, "LEASE_OR_VERSION_LOST");
    }
}
