package com.damai.controller.agent.watch;

/** Fixed-cardinality outcomes exposed by scheduler metrics and execution history. */
public enum WatchCheckOutcome {
    MATCHED,
    NO_MATCH,
    DEPENDENCY_ERROR,
    STALE
}
