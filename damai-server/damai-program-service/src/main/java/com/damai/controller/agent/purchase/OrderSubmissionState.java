package com.damai.controller.agent.purchase;

public enum OrderSubmissionState {
    PENDING,
    PROCESSING,
    RETRY,
    RECONCILE,
    SUBMITTED,
    FAILED,
    MANUAL_REVIEW
}
