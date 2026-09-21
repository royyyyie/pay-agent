package com.damai.controller.agent.watch;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Arrays;
import java.util.List;

/** Versioned Kafka event deliberately excluding tenant and user identity. */
public record WatchNotificationEvent(
        String schemaVersion,
        String eventId,
        Long ruleId,
        Long programId,
        Long ruleVersion,
        String channel,
        List<Long> matchedTicketCategoryIds,
        int matchedCategoryCount,
        long matchedRemaining,
        BigDecimal minimumPrice,
        String freshnessAt) {

    public static final String SCHEMA_VERSION = "damai.watch.notification/v1";

    public static WatchNotificationEvent from(WatchNotificationOutbox outbox) {
        return new WatchNotificationEvent(
                SCHEMA_VERSION,
                outbox.getEventId(),
                outbox.getRuleId(),
                outbox.getProgramId(),
                outbox.getRuleVersion(),
                outbox.getChannel(),
                parseIds(outbox.getMatchedTicketCategoryIds()),
                outbox.getMatchedCategoryCount(),
                outbox.getMatchedRemaining(),
                outbox.getMinimumPrice(),
                outbox.getFreshnessAt().toInstant().toString());
    }

    public void validate() {
        if (!SCHEMA_VERSION.equals(schemaVersion)
                || eventId == null
                || eventId.isBlank()
                || ruleId == null
                || programId == null
                || ruleVersion == null
                || !WatchNotificationChannel.IN_APP.name().equals(channel)
                || matchedTicketCategoryIds == null
                || matchedTicketCategoryIds.isEmpty()
                || matchedCategoryCount <= 0
                || matchedRemaining <= 0
                || minimumPrice == null
                || minimumPrice.signum() < 0
                || freshnessAt == null) {
            throw new IllegalArgumentException("invalid watch notification event");
        }
        Instant.parse(freshnessAt);
    }

    private static List<Long> parseIds(String value) {
        if (value == null || value.isBlank()) {
            return List.of();
        }
        return Arrays.stream(value.split(","))
                .map(String::trim)
                .filter(part -> !part.isBlank())
                .map(Long::valueOf)
                .toList();
    }
}
