package com.damai.controller.agent.purchase;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.dto.AgentPurchaseOrderSubmitRequest;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Date;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolPurchaseOrderSubmissionTest {

    private static final Instant NOW = Instant.parse("2026-09-28T08:00:00Z");

    @Test
    void beginAtomicallyConsumesGrantAndCreatesStableOutboxCommand() {
        PurchaseIntentMapper intentMapper = mock(PurchaseIntentMapper.class);
        ConfirmationGrantMapper grantMapper = mock(ConfirmationGrantMapper.class);
        PurchaseOrderSubmissionMapper submissionMapper = mock(PurchaseOrderSubmissionMapper.class);
        UidGenerator uidGenerator = mock(UidGenerator.class);
        PurchaseOrderSubmissionStore store = new PurchaseOrderSubmissionStore(
                intentMapper,
                grantMapper,
                submissionMapper,
                uidGenerator,
                Clock.fixed(NOW, ZoneOffset.UTC));
        PurchaseIntent intent = confirmedIntent();
        ConfirmationGrant grant = availableGrant();
        when(intentMapper.selectOwnedForUpdate("tenant-1", "42", 7001L, 1001L))
                .thenReturn(intent);
        when(submissionMapper.selectByIntent(7001L, 1001L)).thenReturn(null);
        when(grantMapper.selectByIntentForUpdate(7001L, 1001L)).thenReturn(grant);
        when(uidGenerator.getUid()).thenReturn(9001L);
        when(uidGenerator.getOrderNumber(eq(42L), anyLong())).thenReturn(88001L);
        when(submissionMapper.insertSubmission(any())).thenReturn(1);
        when(grantMapper.consume(eq(8001L), eq(7001L), eq(1001L), any())).thenReturn(1);
        when(intentMapper.startSubmission(
                        eq("tenant-1"),
                        eq("42"),
                        eq("session-1"),
                        eq(7001L),
                        eq(1001L),
                        eq(2L),
                        eq(88001L),
                        any()))
                .thenReturn(1);

        PurchaseOrderSubmission result = store.begin(
                "tenant-1", "42", "session-1", submitRequest());

        ArgumentCaptor<PurchaseOrderSubmission> captured =
                ArgumentCaptor.forClass(PurchaseOrderSubmission.class);
        verify(submissionMapper).insertSubmission(captured.capture());
        assertEquals(88001L, captured.getValue().getOrderNumber());
        assertEquals("9001,9002", captured.getValue().getTicketUserRefs());
        assertEquals(OrderSubmissionState.PENDING.name(), result.getSubmissionState());
        verify(grantMapper).consume(eq(8001L), eq(7001L), eq(1001L), any());
        verify(intentMapper).startSubmission(
                eq("tenant-1"),
                eq("42"),
                eq("session-1"),
                eq(7001L),
                eq(1001L),
                eq(2L),
                eq(88001L),
                any());
    }

    @Test
    void repeatedBeginReturnsTheSameCommandWithoutConsumingAnotherGrant() {
        PurchaseIntentMapper intentMapper = mock(PurchaseIntentMapper.class);
        ConfirmationGrantMapper grantMapper = mock(ConfirmationGrantMapper.class);
        PurchaseOrderSubmissionMapper submissionMapper = mock(PurchaseOrderSubmissionMapper.class);
        PurchaseOrderSubmission existing = processingSubmission();
        when(intentMapper.selectOwnedForUpdate("tenant-1", "42", 7001L, 1001L))
                .thenReturn(confirmedIntent());
        when(submissionMapper.selectByIntent(7001L, 1001L)).thenReturn(existing);
        PurchaseOrderSubmissionStore store = new PurchaseOrderSubmissionStore(
                intentMapper,
                grantMapper,
                submissionMapper,
                mock(UidGenerator.class),
                Clock.fixed(NOW, ZoneOffset.UTC));

        PurchaseOrderSubmission result = store.begin(
                "tenant-1", "42", "session-1", submitRequest());

        assertSame(existing, result);
        verify(grantMapper, never()).consume(any(), any(), any(), any());
        verify(submissionMapper, never()).insertSubmission(any());
    }

    @Test
    void reclaimedUnknownSubmissionNeverBlindlyCreatesASecondOrder() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        submission.setReclaimedUnknown(true);
        submission.setAttemptCount(6);
        when(gateway.find(88001L)).thenReturn(Optional.empty());
        when(store.markTerminal(eq(submission), any(), eq(true),
                eq("AMBIGUOUS_CREATE_WITHOUT_ORDER_FACT"))).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(gateway, never()).create(any());
        verify(store).markTerminal(
                eq(submission), any(), eq(true), eq("AMBIGUOUS_CREATE_WITHOUT_ORDER_FACT"));
    }

    @Test
    void unknownSubmissionRetriesOnlyTheReadSideBeforeManualReview() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        submission.setReclaimedUnknown(true);
        submission.setAttemptCount(2);
        when(gateway.find(88001L)).thenReturn(Optional.empty());
        when(store.markReconcile(eq(submission), any(), any(),
                eq("RECONCILE_ORDER_FACT_NOT_FOUND"))).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(gateway, never()).create(any());
        verify(store).markReconcile(
                eq(submission), any(), any(), eq("RECONCILE_ORDER_FACT_NOT_FOUND"));
        verify(store, never()).markTerminal(any(), any(), anyBoolean(), any());
    }

    @Test
    void existingMatchingOrderFactIsReconciledWithoutCreatingAgain() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        Date createdAt = Date.from(NOW.minusSeconds(3));
        when(gateway.find(88001L)).thenReturn(Optional.of(new PurchaseOrderFact(
                88001L, 1001L, 42L, new BigDecimal("1160.00"), 1, createdAt)));
        when(store.markSubmitted(submission, createdAt)).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(gateway, never()).create(any());
        verify(store).markSubmitted(submission, createdAt);
    }

    @Test
    void mismatchedOrderFactIsEscalatedForManualReview() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        when(gateway.find(88001L)).thenReturn(Optional.of(new PurchaseOrderFact(
                88001L, 9999L, 42L, new BigDecimal("1160.00"), 1, Date.from(NOW))));
        when(store.markTerminal(eq(submission), any(), eq(true),
                eq("ORDER_FACT_BINDING_MISMATCH"))).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(gateway, never()).create(any());
        verify(store).markTerminal(
                eq(submission), any(), eq(true), eq("ORDER_FACT_BINDING_MISMATCH"));
    }

    @Test
    void ambiguousCreateFailureBecomesReconciliationOnly() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        when(gateway.find(88001L)).thenReturn(Optional.empty());
        when(gateway.create(submission)).thenThrow(
                new PurchaseOrderGatewayException("PROGRAM_TEMPORARY", true, null));
        when(store.markReconcile(eq(submission), any(), any(),
                eq("AMBIGUOUS_PROGRAM_TEMPORARY"))).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(store).markReconcile(
                eq(submission), any(), any(), eq("AMBIGUOUS_PROGRAM_TEMPORARY"));
        verify(store, never()).markRetry(any(), any(), any(), any());
    }

    @Test
    void failedPreflightLookupCanRetryWithoutCallingCreate() {
        PurchaseOrderSubmissionStore store = mock(PurchaseOrderSubmissionStore.class);
        PurchaseOrderGateway gateway = mock(PurchaseOrderGateway.class);
        PurchaseOrderSubmission submission = processingSubmission();
        when(gateway.find(88001L)).thenThrow(
                new PurchaseOrderGatewayException("ORDER_FACT_UNAVAILABLE", true, null));
        when(store.markRetry(eq(submission), any(), any(),
                eq("SAFE_RETRY_ORDER_FACT_UNAVAILABLE"))).thenReturn(true);
        PurchaseOrderSubmissionProcessor processor = processor(store, gateway);

        processor.process(submission);

        verify(gateway, never()).create(any());
        verify(store).markRetry(
                eq(submission), any(), any(), eq("SAFE_RETRY_ORDER_FACT_UNAVAILABLE"));
    }

    @Test
    void reconciliationCommandIsClaimedAsUnknown() {
        PurchaseIntentMapper intentMapper = mock(PurchaseIntentMapper.class);
        ConfirmationGrantMapper grantMapper = mock(ConfirmationGrantMapper.class);
        PurchaseOrderSubmissionMapper submissionMapper = mock(PurchaseOrderSubmissionMapper.class);
        PurchaseOrderSubmission candidate = processingSubmission();
        candidate.setSubmissionState(OrderSubmissionState.RECONCILE.name());
        candidate.setNextAttemptTime(Date.from(NOW.minusSeconds(1)));
        when(submissionMapper.tryClaim(
                        eq(9001L), eq(1001L), any(), eq("worker-1"), any(), any()))
                .thenReturn(1);
        PurchaseOrderSubmissionStore store = new PurchaseOrderSubmissionStore(
                intentMapper,
                grantMapper,
                submissionMapper,
                mock(UidGenerator.class),
                Clock.fixed(NOW, ZoneOffset.UTC));

        PurchaseOrderSubmission claimed = store.claimOne(
                candidate, "worker-1", java.time.Duration.ofSeconds(30));

        assertEquals(true, claimed.isReclaimedUnknown());
    }

    @Test
    void securityGateRequiresPurchaseIntentAndStrongDelegationKey() {
        assertThrows(
                IllegalStateException.class,
                () -> new PurchaseOrderSecurityConfiguration(
                        false, "d".repeat(32), "f".repeat(32)));
        assertThrows(
                IllegalStateException.class,
                () -> new PurchaseOrderSecurityConfiguration(
                        true, "weak", "f".repeat(32)));
        assertThrows(
                IllegalStateException.class,
                () -> new PurchaseOrderSecurityConfiguration(
                        true, "d".repeat(32), "weak"));
        new PurchaseOrderSecurityConfiguration(
                true, "d".repeat(32), "f".repeat(32));
    }

    private PurchaseOrderSubmissionProcessor processor(
            PurchaseOrderSubmissionStore store, PurchaseOrderGateway gateway) {
        return new PurchaseOrderSubmissionProcessor(
                store,
                gateway,
                new SimpleMeterRegistry(),
                Clock.fixed(NOW, ZoneOffset.UTC),
                5,
                6);
    }

    private static AgentPurchaseOrderSubmitRequest submitRequest() {
        AgentPurchaseOrderSubmitRequest request = new AgentPurchaseOrderSubmitRequest();
        request.setIntentId(7001L);
        request.setProgramId(1001L);
        request.setExpectedVersion(2L);
        return request;
    }

    private static PurchaseIntent confirmedIntent() {
        PurchaseIntent intent = new PurchaseIntent();
        intent.setId(7001L);
        intent.setTenantId("tenant-1");
        intent.setUserId("42");
        intent.setSessionKey("session-1");
        intent.setProgramId(1001L);
        intent.setTicketCategoryId(3001L);
        intent.setQuantity(2);
        intent.setTicketUserRefs("9001,9002");
        intent.setUnitAmountFen(58000L);
        intent.setTotalAmountFen(116000L);
        intent.setCurrency("CNY");
        intent.setQuoteHash("a".repeat(64));
        intent.setIntentState(PurchaseIntentState.CONFIRMED.name());
        intent.setVersion(2L);
        return intent;
    }

    private static ConfirmationGrant availableGrant() {
        ConfirmationGrant grant = new ConfirmationGrant();
        grant.setId(8001L);
        grant.setIntentId(7001L);
        grant.setProgramId(1001L);
        grant.setIntentVersion(2L);
        grant.setTenantId("tenant-1");
        grant.setUserId("42");
        grant.setSessionKey("session-1");
        grant.setQuoteHash("a".repeat(64));
        grant.setGrantState(ConfirmationGrantState.AVAILABLE.name());
        grant.setExpiresAt(Date.from(NOW.plusSeconds(60)));
        return grant;
    }

    private static PurchaseOrderSubmission processingSubmission() {
        PurchaseOrderSubmission submission = new PurchaseOrderSubmission();
        submission.setId(9001L);
        submission.setIntentId(7001L);
        submission.setProgramId(1001L);
        submission.setIntentVersion(3L);
        submission.setTenantId("tenant-1");
        submission.setUserId("42");
        submission.setSessionKey("session-1");
        submission.setTicketCategoryId(3001L);
        submission.setQuantity(2);
        submission.setTicketUserRefs("9001,9002");
        submission.setUnitAmountFen(58000L);
        submission.setTotalAmountFen(116000L);
        submission.setCurrency("CNY");
        submission.setOrderNumber(88001L);
        submission.setSubmissionState(OrderSubmissionState.PROCESSING.name());
        submission.setAttemptCount(1);
        submission.setLeaseOwner("worker-1");
        submission.setLeaseToken("lease-1");
        return submission;
    }
}
