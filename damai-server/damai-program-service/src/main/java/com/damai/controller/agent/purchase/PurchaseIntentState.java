package com.damai.controller.agent.purchase;

public enum PurchaseIntentState {
    PENDING_CONFIRMATION,
    CONFIRMED,
    CANCELLED,
    EXPIRED,
    SUBMITTING,
    SUBMITTED,
    SUBMISSION_FAILED,
    SUBMISSION_UNKNOWN
}
