package com.damai.controller.agent;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.watch.WatchCheckOutcome;
import com.damai.controller.agent.watch.WatchEvaluation;
import com.damai.controller.agent.watch.WatchEventTransport;
import com.damai.controller.agent.watch.WatchNotification;
import com.damai.controller.agent.watch.WatchNotificationConsumeResult;
import com.damai.controller.agent.watch.WatchNotificationDeliveryState;
import com.damai.controller.agent.watch.WatchNotificationEvent;
import com.damai.controller.agent.watch.WatchNotificationMapper;
import com.damai.controller.agent.watch.WatchNotificationOutbox;
import com.damai.controller.agent.watch.WatchNotificationOutboxMapper;
import com.damai.controller.agent.watch.WatchNotificationService;
import com.damai.controller.agent.watch.WatchOutboxPublisher;
import com.damai.controller.agent.watch.WatchOutboxService;
import com.damai.controller.agent.watch.WatchOutboxState;
import com.damai.controller.agent.watch.WatchRule;
import com.damai.controller.agent.watch.WatchRuleExecution;
import com.damai.controller.agent.watch.WatchRuleExecutionMapper;
import com.damai.controller.agent.watch.WatchRuleLeaseService;
import com.damai.controller.agent.watch.WatchRuleMapper;
import com.damai.controller.agent.watch.WatchRuleState;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Date;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolWatchOutboxTest {

    private static final Instant NOW = Instant.parse("2026-09-21T08:00:00Z");

    @Test
    void matchedCompletionWritesExecutionAndDeduplicatedOutboxAfterFence() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchRuleExecutionMapper executionMapper = mock(WatchRuleExecutionMapper.class);
        WatchNotificationOutboxMapper outboxMapper = mock(WatchNotificationOutboxMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        WatchRuleLeaseService service = new WatchRuleLeaseService(
                ruleMapper,
                executionMapper,
                outboxMapper,
                uidGenerator,
                Duration.ofMinutes(15),
                true);
        WatchRule rule = claimedRule();
        Date checkedAt = Date.from(NOW);
        Date nextCheckAt = Date.from(NOW.plusSeconds(300));
        when(uidGenerator.getUid()).thenReturn(7001L, 7002L);
        when(ruleMapper.completeClaim(
                        eq(9001L),
                        eq(1001L),
                        eq(4L),
                        eq("replica-a"),
                        eq("lease-token"),
                        eq(checkedAt),
                        eq(nextCheckAt),
                        eq(checkedAt)))
                .thenReturn(1);
        when(executionMapper.insertIdempotent(any())).thenReturn(1);
        when(outboxMapper.insertIdempotent(any())).thenReturn(1);
        WatchEvaluation match = new WatchEvaluation(
                WatchCheckOutcome.MATCHED,
                "3,8",
                2,
                7,
                new BigDecimal("580.00"),
                null);

        assertTrue(service.complete(rule, match, checkedAt, nextCheckAt));

        ArgumentCaptor<WatchRuleExecution> execution =
                ArgumentCaptor.forClass(WatchRuleExecution.class);
        ArgumentCaptor<WatchNotificationOutbox> outbox =
                ArgumentCaptor.forClass(WatchNotificationOutbox.class);
        InOrder transactionOrder = inOrder(ruleMapper, executionMapper, outboxMapper);
        transactionOrder.verify(ruleMapper).completeClaim(
                9001L,
                1001L,
                4L,
                "replica-a",
                "lease-token",
                checkedAt,
                nextCheckAt,
                checkedAt);
        transactionOrder.verify(executionMapper).insertIdempotent(execution.capture());
        transactionOrder.verify(outboxMapper).insertIdempotent(outbox.capture());
        assertEquals(7001L, execution.getValue().getId());
        assertEquals(7001L, outbox.getValue().getExecutionId());
        assertEquals(7002L, outbox.getValue().getId());
        assertEquals(64, outbox.getValue().getConditionFingerprint().length());
        assertEquals(64, outbox.getValue().getDedupeKey().length());
        assertEquals("watch-" + outbox.getValue().getDedupeKey(), outbox.getValue().getEventId());
        assertEquals(WatchOutboxState.PENDING.name(), outbox.getValue().getPublishState());
    }

    @Test
    void cooldownSuppressesRepeatedMatchWithoutSlidingTheWindow() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchRuleExecutionMapper executionMapper = mock(WatchRuleExecutionMapper.class);
        WatchNotificationOutboxMapper outboxMapper = mock(WatchNotificationOutboxMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        WatchRuleLeaseService service = new WatchRuleLeaseService(
                ruleMapper,
                executionMapper,
                outboxMapper,
                uidGenerator,
                Duration.ofMinutes(15),
                true);
        WatchRule rule = claimedRule();
        rule.setLastTriggeredTime(Date.from(NOW.minusSeconds(60)));
        Date checkedAt = Date.from(NOW);
        Date nextCheckAt = Date.from(NOW.plusSeconds(300));
        when(uidGenerator.getUid()).thenReturn(7001L);
        when(ruleMapper.completeClaim(
                        eq(9001L),
                        eq(1001L),
                        eq(4L),
                        eq("replica-a"),
                        eq("lease-token"),
                        eq(checkedAt),
                        eq(nextCheckAt),
                        eq(null)))
                .thenReturn(1);
        when(executionMapper.insertIdempotent(any())).thenReturn(1);

        assertTrue(service.complete(
                rule,
                new WatchEvaluation(
                        WatchCheckOutcome.MATCHED,
                        "3",
                        1,
                        3,
                        new BigDecimal("580.00"),
                        null),
                checkedAt,
                nextCheckAt));

        verify(outboxMapper, never()).insertIdempotent(any());
    }

    @Test
    void disabledNotificationFeatureDoesNotCreateOutbox() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchRuleExecutionMapper executionMapper = mock(WatchRuleExecutionMapper.class);
        WatchNotificationOutboxMapper outboxMapper = mock(WatchNotificationOutboxMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        WatchRuleLeaseService service = new WatchRuleLeaseService(
                ruleMapper,
                executionMapper,
                outboxMapper,
                uidGenerator,
                Duration.ofMinutes(15),
                false);
        WatchRule rule = claimedRule();
        Date checkedAt = Date.from(NOW);
        Date nextCheckAt = Date.from(NOW.plusSeconds(300));
        when(uidGenerator.getUid()).thenReturn(7001L);
        when(ruleMapper.completeClaim(
                        eq(9001L),
                        eq(1001L),
                        eq(4L),
                        eq("replica-a"),
                        eq("lease-token"),
                        eq(checkedAt),
                        eq(nextCheckAt),
                        eq(null)))
                .thenReturn(1);
        when(executionMapper.insertIdempotent(any())).thenReturn(1);

        assertTrue(service.complete(
                rule,
                new WatchEvaluation(
                        WatchCheckOutcome.MATCHED,
                        "3",
                        1,
                        3,
                        new BigDecimal("580.00"),
                        null),
                checkedAt,
                nextCheckAt));

        verify(outboxMapper, never()).insertIdempotent(any());
    }

    @Test
    void publisherAcknowledgesKafkaBeforeMarkingOutboxPublished() {
        WatchOutboxService outboxService = mock(WatchOutboxService.class);
        WatchEventTransport transport = mock(WatchEventTransport.class);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        WatchOutboxPublisher publisher = publisher(outboxService, transport, registry, 3);
        WatchNotificationOutbox outbox = outbox(0);
        when(outboxService.countDue(any())).thenReturn(1L);
        when(outboxService.claimDue(any(), eq("publisher-a"), any(), eq(4), eq(50)))
                .thenReturn(List.of(outbox));
        when(outboxService.markPublished(eq(outbox), any())).thenReturn(true);

        assertEquals(1, publisher.runOnce());

        ArgumentCaptor<WatchNotificationEvent> event =
                ArgumentCaptor.forClass(WatchNotificationEvent.class);
        InOrder order = inOrder(transport, outboxService);
        order.verify(transport).publish(event.capture());
        order.verify(outboxService).markPublished(eq(outbox), any());
        assertEquals(1001L, event.getValue().programId());
        assertEquals(List.of(3L, 8L), event.getValue().matchedTicketCategoryIds());
        assertEquals(
                1.0,
                registry.get("damai.agent.watch.outbox.published").counter().count());
    }

    @Test
    void publisherRetriesThenMovesExhaustedEventToDeadState() {
        WatchOutboxService retryService = mock(WatchOutboxService.class);
        WatchEventTransport failedTransport = event -> {
            throw new IllegalStateException("Kafka unavailable");
        };
        SimpleMeterRegistry retryRegistry = new SimpleMeterRegistry();
        WatchOutboxPublisher retryPublisher =
                publisher(retryService, failedTransport, retryRegistry, 3);
        WatchNotificationOutbox firstAttempt = outbox(0);
        when(retryService.countDue(any())).thenReturn(1L);
        when(retryService.claimDue(any(), any(), any(), anyInt(), anyInt()))
                .thenReturn(List.of(firstAttempt));
        when(retryService.markFailed(eq(firstAttempt), any(), any(), eq(3))).thenReturn(true);

        retryPublisher.runOnce();

        assertEquals(
                1.0,
                retryRegistry.get("damai.agent.watch.outbox.retried").counter().count());

        WatchOutboxService deadService = mock(WatchOutboxService.class);
        SimpleMeterRegistry deadRegistry = new SimpleMeterRegistry();
        WatchOutboxPublisher deadPublisher =
                publisher(deadService, failedTransport, deadRegistry, 3);
        WatchNotificationOutbox finalAttempt = outbox(2);
        when(deadService.countDue(any())).thenReturn(1L);
        when(deadService.claimDue(any(), any(), any(), anyInt(), anyInt()))
                .thenReturn(List.of(finalAttempt));
        when(deadService.markFailed(eq(finalAttempt), any(), any(), eq(3))).thenReturn(true);

        deadPublisher.runOnce();

        assertEquals(
                1.0,
                deadRegistry.get("damai.agent.watch.outbox.dead").counter().count());
    }

    @Test
    void outboxServicePersistsDeadStateAfterMaximumAttempts() {
        WatchNotificationOutboxMapper mapper = mock(WatchNotificationOutboxMapper.class);
        WatchOutboxService service = new WatchOutboxService(mapper);
        WatchNotificationOutbox finalAttempt = outbox(2);
        Date failedAt = Date.from(NOW);
        Date nextAttempt = Date.from(NOW.plusSeconds(20));
        when(mapper.markFailed(
                        eq(7002L),
                        eq(1001L),
                        eq("publisher-a"),
                        eq("outbox-token"),
                        eq(WatchOutboxState.DEAD.name()),
                        eq(nextAttempt),
                        eq("KAFKA_PUBLISH_FAILED"),
                        eq(failedAt)))
                .thenReturn(1);

        assertTrue(service.markFailed(finalAttempt, failedAt, nextAttempt, 3));
    }

    @Test
    void consumerIsIdempotentAndPausedRuleIsSuppressed() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchNotificationMapper notificationMapper = mock(WatchNotificationMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        WatchNotificationService service =
                new WatchNotificationService(ruleMapper, notificationMapper, uidGenerator);
        WatchRule active = currentRule(WatchRuleState.ACTIVE);
        WatchNotificationEvent event = WatchNotificationEvent.from(outbox(0));
        when(ruleMapper.selectCurrent(9001L, 1001L)).thenReturn(active);
        when(uidGenerator.getUid()).thenReturn(8001L, 8002L, 8003L);
        when(notificationMapper.insertIdempotent(any())).thenReturn(1, 0, 1);

        assertEquals(WatchNotificationConsumeResult.DELIVERED, service.consume(event));
        assertEquals(WatchNotificationConsumeResult.DUPLICATE, service.consume(event));

        WatchRule paused = currentRule(WatchRuleState.PAUSED);
        when(ruleMapper.selectCurrent(9001L, 1001L)).thenReturn(paused);
        assertEquals(WatchNotificationConsumeResult.SUPPRESSED, service.consume(event));

        ArgumentCaptor<WatchNotification> receipts =
                ArgumentCaptor.forClass(WatchNotification.class);
        verify(notificationMapper, org.mockito.Mockito.times(3))
                .insertIdempotent(receipts.capture());
        assertEquals(
                WatchNotificationDeliveryState.DELIVERED.name(),
                receipts.getAllValues().get(0).getDeliveryState());
        assertEquals(
                WatchNotificationDeliveryState.SUPPRESSED.name(),
                receipts.getAllValues().get(2).getDeliveryState());
    }

    @Test
    void kafkaPayloadContainsNoTenantOrUserIdentity() throws Exception {
        WatchNotificationEvent event = WatchNotificationEvent.from(outbox(0));
        String payload = new ObjectMapper().writeValueAsString(event);

        assertFalse(payload.contains("tenant"));
        assertFalse(payload.contains("user"));
        assertTrue(payload.contains("freshnessAt"));
        assertNotEquals(-1, payload.indexOf("matchedTicketCategoryIds"));
    }

    private WatchOutboxPublisher publisher(
            WatchOutboxService outboxService,
            WatchEventTransport transport,
            SimpleMeterRegistry registry,
            int maximumAttempts) {
        return new WatchOutboxPublisher(
                outboxService,
                transport,
                registry,
                Clock.fixed(NOW, ZoneOffset.UTC),
                "publisher-a",
                30,
                5,
                maximumAttempts,
                4,
                50);
    }

    private WatchRule claimedRule() {
        WatchRule rule = currentRule(WatchRuleState.ACTIVE);
        rule.setTicketCategoryIds("3,8");
        rule.setMaxPrice(new BigDecimal("680.00"));
        rule.setMinRemaining(2L);
        rule.setLeaseOwner("replica-a");
        rule.setLeaseToken("lease-token");
        rule.setLeaseExpiresAt(Date.from(NOW.plusSeconds(60)));
        rule.setClaimedVersion(rule.getVersion());
        return rule;
    }

    private WatchRule currentRule(WatchRuleState state) {
        WatchRule rule = new WatchRule();
        rule.setId(9001L);
        rule.setProgramId(1001L);
        rule.setTenantId("tenant-1");
        rule.setUserId("user-1");
        rule.setVersion(4L);
        rule.setRuleState(state.name());
        rule.setNotificationChannel("IN_APP");
        return rule;
    }

    private WatchNotificationOutbox outbox(int attempts) {
        WatchNotificationOutbox outbox = new WatchNotificationOutbox();
        outbox.setId(7002L);
        outbox.setEventId("watch-0123456789abcdef");
        outbox.setRuleId(9001L);
        outbox.setProgramId(1001L);
        outbox.setRuleVersion(4L);
        outbox.setChannel("IN_APP");
        outbox.setMatchedTicketCategoryIds("3,8");
        outbox.setMatchedCategoryCount(2);
        outbox.setMatchedRemaining(7L);
        outbox.setMinimumPrice(new BigDecimal("580.00"));
        outbox.setFreshnessAt(Date.from(NOW));
        outbox.setAttemptCount(attempts);
        outbox.setLeaseOwner("publisher-a");
        outbox.setLeaseToken("outbox-token");
        return outbox;
    }
}
