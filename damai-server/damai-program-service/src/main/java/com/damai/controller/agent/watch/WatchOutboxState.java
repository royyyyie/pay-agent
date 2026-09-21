package com.damai.controller.agent.watch;

public enum WatchOutboxState {
    PENDING,
    RETRY,
    PUBLISHED,
    DEAD
}
