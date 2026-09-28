package com.damai.controller.agent.purchase;

public enum PurchaseSubmissionOutcome {
    SUBMITTED,
    RECONCILED,
    RETRY,
    RECONCILE,
    FAILED,
    MANUAL_REVIEW,
    STALE
}
