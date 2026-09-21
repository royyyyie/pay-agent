package com.damai.controller.agent.watch;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.Map;

@Component
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = {"enabled", "notification-enabled"},
        havingValue = "true")
public class WatchNotificationConsumer {

    private final ObjectMapper objectMapper;
    private final WatchNotificationService service;
    private final Map<WatchNotificationConsumeResult, Counter> counters;

    public WatchNotificationConsumer(
            ObjectMapper objectMapper,
            WatchNotificationService service,
            MeterRegistry registry) {
        this.objectMapper = objectMapper;
        this.service = service;
        this.counters = new EnumMap<>(WatchNotificationConsumeResult.class);
        for (WatchNotificationConsumeResult result : WatchNotificationConsumeResult.values()) {
            counters.put(
                    result,
                    Counter.builder("damai.agent.watch.notification.consumed")
                            .tag("result", result.name().toLowerCase())
                            .register(registry));
        }
    }

    @KafkaListener(
            topics = "${agent.watch-rules.notification-topic:damai-agent-watch-notification}",
            groupId = "${agent.watch-rules.notification-consumer-group:damai-watch-notification-v1}",
            containerFactory = "watchNotificationKafkaListenerContainerFactory")
    public void consume(String payload) throws JsonProcessingException {
        WatchNotificationEvent event = objectMapper.readValue(
                payload, WatchNotificationEvent.class);
        WatchNotificationConsumeResult result = service.consume(event);
        counters.get(result).increment();
    }
}
