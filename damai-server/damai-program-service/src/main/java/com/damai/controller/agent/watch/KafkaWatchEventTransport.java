package com.damai.controller.agent.watch;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/** Synchronous hand-off lets the outbox retain ownership until Kafka acknowledges the record. */
@Component
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = {"enabled", "notification-enabled"},
        havingValue = "true")
public class KafkaWatchEventTransport implements WatchEventTransport {

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final ObjectMapper objectMapper;
    private final String topic;
    private final long timeoutMillis;

    public KafkaWatchEventTransport(
            KafkaTemplate<String, String> kafkaTemplate,
            ObjectMapper objectMapper,
            @Value("${agent.watch-rules.notification-topic:damai-agent-watch-notification}")
                    String topic,
            @Value("${agent.watch-rules.notification-publish-timeout-ms:5000}")
                    long timeoutMillis) {
        this.kafkaTemplate = kafkaTemplate;
        this.objectMapper = objectMapper;
        this.topic = topic;
        this.timeoutMillis = Math.max(100, timeoutMillis);
    }

    @Override
    public void publish(WatchNotificationEvent event) {
        try {
            String payload = objectMapper.writeValueAsString(event);
            kafkaTemplate.send(topic, event.eventId(), payload)
                    .get(timeoutMillis, TimeUnit.MILLISECONDS);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("watch event serialization failed", exception);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("watch event Kafka publish failed", exception);
        } catch (Exception exception) {
            throw new IllegalStateException("watch event Kafka publish failed", exception);
        }
    }
}
