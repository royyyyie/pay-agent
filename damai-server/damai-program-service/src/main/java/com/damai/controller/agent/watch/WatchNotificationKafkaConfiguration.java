package com.damai.controller.agent.watch;

import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

/** Dedicated retry/DLT policy so poison watch events do not affect unrelated listeners. */
@Configuration
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = {"enabled", "notification-enabled"},
        havingValue = "true")
public class WatchNotificationKafkaConfiguration {

    @Bean("watchNotificationKafkaListenerContainerFactory")
    public ConcurrentKafkaListenerContainerFactory<String, String> watchNotificationFactory(
            ConsumerFactory<String, String> consumerFactory,
            KafkaTemplate<String, String> kafkaTemplate,
            @Value("${agent.watch-rules.notification-topic:damai-agent-watch-notification}")
                    String topic,
            @Value("${agent.watch-rules.notification-consumer-backoff-ms:1000}")
                    long backoffMillis,
            @Value("${agent.watch-rules.notification-consumer-retries:2}")
                    long retryCount) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, exception) -> new TopicPartition(topic + ".dlt", record.partition()));
        DefaultErrorHandler errorHandler = new DefaultErrorHandler(
                recoverer,
                new FixedBackOff(Math.max(100, backoffMillis), Math.max(0, retryCount)));
        ConcurrentKafkaListenerContainerFactory<String, String> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.setCommonErrorHandler(errorHandler);
        return factory;
    }
}
