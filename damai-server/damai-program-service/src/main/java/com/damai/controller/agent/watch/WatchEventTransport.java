package com.damai.controller.agent.watch;

@FunctionalInterface
public interface WatchEventTransport {
    void publish(WatchNotificationEvent event);
}
