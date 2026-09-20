package com.damai.controller.agent;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.watch.WatchCheckOutcome;
import com.damai.controller.agent.watch.WatchEvaluation;
import com.damai.controller.agent.watch.WatchRule;
import com.damai.controller.agent.watch.WatchRuleExecution;
import com.damai.controller.agent.watch.WatchRuleExecutionMapper;
import com.damai.controller.agent.watch.WatchRuleLeaseService;
import com.damai.controller.agent.watch.WatchRuleMapper;
import com.damai.controller.agent.watch.WatchRuleScheduler;
import com.damai.controller.agent.watch.WatchRuleState;
import com.damai.service.TicketCategoryService;
import com.damai.vo.TicketCategoryDetailVo;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Date;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolWatchRuleSchedulerTest {

    private static final Instant NOW = Instant.parse("2026-09-20T10:00:00Z");

    private WatchRuleLeaseService leaseService;
    private TicketCategoryService ticketCategoryService;
    private SimpleMeterRegistry meterRegistry;
    private WatchRuleScheduler scheduler;

    @BeforeEach
    void setUp() {
        leaseService = mock(WatchRuleLeaseService.class);
        ticketCategoryService = mock(TicketCategoryService.class);
        meterRegistry = new SimpleMeterRegistry();
        scheduler = new WatchRuleScheduler(
                leaseService,
                ticketCategoryService,
                meterRegistry,
                Clock.fixed(NOW, ZoneOffset.UTC),
                "replica-a",
                60,
                4,
                50);
    }

    @Test
    void evaluatesAllowlistPriceAndRemainingFromOneBatchRead() {
        WatchRule rule = claimedRule();
        when(leaseService.countDue(any())).thenReturn(1L);
        when(leaseService.claimDue(any(), anyString(), any(), anyInt(), anyInt()))
                .thenReturn(List.of(rule));
        when(ticketCategoryService.selectListByPrograms(List.of(1001L)))
                .thenReturn(Map.of(
                        1001L,
                        List.of(
                                ticket(3L, "580.00", 3),
                                ticket(8L, "780.00", 20),
                                ticket(9L, "200.00", 20))));
        when(leaseService.complete(eq(rule), any(), any(), any())).thenReturn(true);

        assertEquals(1, scheduler.runOnce());

        ArgumentCaptor<WatchEvaluation> result = ArgumentCaptor.forClass(WatchEvaluation.class);
        verify(leaseService).complete(eq(rule), result.capture(), any(), any());
        assertEquals(WatchCheckOutcome.MATCHED, result.getValue().outcome());
        assertEquals("3", result.getValue().matchedTicketCategoryIds());
        assertEquals(1, result.getValue().matchedCategoryCount());
        assertEquals(3, result.getValue().matchedRemaining());
        assertEquals(new BigDecimal("580.00"), result.getValue().minimumPrice());
        assertEquals(
                1.0,
                meterRegistry.get("damai.agent.watch.scheduler.executions")
                        .tag("outcome", "matched")
                        .counter()
                        .count());
    }

    @Test
    void inventoryFailureIsPersistedWithABoundedErrorCode() {
        WatchRule rule = claimedRule();
        when(leaseService.countDue(any())).thenReturn(1L);
        when(leaseService.claimDue(any(), anyString(), any(), anyInt(), anyInt()))
                .thenReturn(List.of(rule));
        when(ticketCategoryService.selectListByPrograms(any()))
                .thenThrow(new IllegalStateException("do not persist this message"));
        when(leaseService.complete(eq(rule), any(), any(), any())).thenReturn(true);

        assertEquals(1, scheduler.runOnce());

        ArgumentCaptor<WatchEvaluation> result = ArgumentCaptor.forClass(WatchEvaluation.class);
        verify(leaseService).complete(eq(rule), result.capture(), any(), any());
        assertEquals(WatchCheckOutcome.DEPENDENCY_ERROR, result.getValue().outcome());
        assertEquals("TICKET_INVENTORY_UNAVAILABLE", result.getValue().errorCode());
        assertEquals(
                1.0,
                meterRegistry.get("damai.agent.watch.scheduler.dependency.errors")
                        .tag("dependency", "ticket_inventory")
                        .counter()
                        .count());
    }

    @Test
    void staleCompletionCannotBecomeAnAcceptedMatch() {
        WatchRule rule = claimedRule();
        when(leaseService.countDue(any())).thenReturn(1L);
        when(leaseService.claimDue(any(), anyString(), any(), anyInt(), anyInt()))
                .thenReturn(List.of(rule));
        when(ticketCategoryService.selectListByPrograms(any()))
                .thenReturn(Map.of(1001L, List.of(ticket(3L, "580.00", 3))));
        when(leaseService.complete(eq(rule), any(), any(), any())).thenReturn(false);

        scheduler.runOnce();

        verify(leaseService).recordStale(eq(rule), any());
        assertEquals(
                1.0,
                meterRegistry.get("damai.agent.watch.scheduler.executions")
                        .tag("outcome", "stale")
                        .counter()
                        .count());
        assertEquals(
                0.0,
                meterRegistry.get("damai.agent.watch.scheduler.executions")
                        .tag("outcome", "matched")
                        .counter()
                        .count());
    }

    @Test
    void databaseLeaseUsesAtomicClaimAndFencedCompletion() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchRuleExecutionMapper executionMapper = mock(WatchRuleExecutionMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        WatchRuleLeaseService databaseLeases =
                new WatchRuleLeaseService(ruleMapper, executionMapper, uidGenerator);
        WatchRule candidate = dueRule();
        Date now = Date.from(NOW);
        when(ruleMapper.selectDueCandidates(now, 0, 1, 1)).thenReturn(List.of(candidate));
        when(ruleMapper.tryClaim(
                        eq(candidate.getId()),
                        eq(candidate.getProgramId()),
                        eq(candidate.getVersion()),
                        eq(now),
                        eq("replica-a"),
                        anyString(),
                        any()))
                .thenReturn(1);

        WatchRule claimed = databaseLeases.claimDue(
                        now, "replica-a", Duration.ofSeconds(60), 1, 1)
                .get(0);

        assertNotNull(claimed.getLeaseToken());
        assertEquals(4L, claimed.getClaimedVersion());
        assertTrue(claimed.getLeaseExpiresAt().after(now));

        when(ruleMapper.completeClaim(
                        eq(claimed.getId()),
                        eq(claimed.getProgramId()),
                        eq(4L),
                        eq("replica-a"),
                        eq(claimed.getLeaseToken()),
                        eq(now),
                        any()))
                .thenReturn(1);
        when(uidGenerator.getUid()).thenReturn(7001L);
        when(executionMapper.insertIdempotent(any())).thenReturn(1);
        WatchEvaluation evaluation = new WatchEvaluation(
                WatchCheckOutcome.NO_MATCH, null, 0, 0, null, null);

        assertTrue(databaseLeases.complete(
                claimed, evaluation, now, Date.from(NOW.plusSeconds(300))));
        ArgumentCaptor<WatchRuleExecution> execution =
                ArgumentCaptor.forClass(WatchRuleExecution.class);
        verify(executionMapper).insertIdempotent(execution.capture());
        assertEquals(4L, execution.getValue().getRuleVersion());
        assertEquals(claimed.getLeaseToken(), execution.getValue().getLeaseToken());
    }

    @Test
    void rejectedFenceDoesNotPersistAnAcceptedExecution() {
        WatchRuleMapper ruleMapper = mock(WatchRuleMapper.class);
        WatchRuleExecutionMapper executionMapper = mock(WatchRuleExecutionMapper.class);
        WatchRuleLeaseService databaseLeases = new WatchRuleLeaseService(
                ruleMapper, executionMapper, mock(UidGenerator.class));
        WatchRule claimed = claimedRule();
        when(ruleMapper.completeClaim(
                        anyLong(), anyLong(), anyLong(), anyString(), anyString(), any(), any()))
                .thenReturn(0);

        boolean accepted = databaseLeases.complete(
                claimed,
                new WatchEvaluation(WatchCheckOutcome.MATCHED, "3", 1, 3, BigDecimal.TEN, null),
                Date.from(NOW),
                Date.from(NOW.plusSeconds(300)));

        assertEquals(false, accepted);
        verify(executionMapper, never()).insertIdempotent(any());
    }

    private WatchRule claimedRule() {
        WatchRule rule = dueRule();
        rule.setTicketCategoryIds("3,8");
        rule.setMaxPrice(new BigDecimal("680.00"));
        rule.setMinRemaining(2L);
        rule.setLeaseOwner("replica-a");
        rule.setLeaseToken("lease-token");
        rule.setLeaseExpiresAt(Date.from(NOW.plusSeconds(60)));
        rule.setClaimedVersion(rule.getVersion());
        return rule;
    }

    private WatchRule dueRule() {
        WatchRule rule = new WatchRule();
        rule.setId(9001L);
        rule.setProgramId(1001L);
        rule.setVersion(4L);
        rule.setRuleState(WatchRuleState.ACTIVE.name());
        rule.setCheckIntervalSeconds(300);
        rule.setNextCheckTime(Date.from(NOW.minusSeconds(5)));
        return rule;
    }

    private TicketCategoryDetailVo ticket(Long id, String price, long remaining) {
        TicketCategoryDetailVo ticket = new TicketCategoryDetailVo();
        ticket.setId(id);
        ticket.setProgramId(1001L);
        ticket.setPrice(new BigDecimal(price));
        ticket.setRemainNumber(remaining);
        return ticket;
    }
}
