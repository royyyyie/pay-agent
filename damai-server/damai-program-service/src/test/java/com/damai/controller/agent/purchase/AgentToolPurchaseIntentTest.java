package com.damai.controller.agent.purchase;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.dto.AgentPurchaseConfirmationRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentCancelRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentPrepareRequest;
import com.damai.controller.agent.vo.AgentPurchaseIntentVo;
import com.damai.service.TicketCategoryService;
import com.damai.vo.TicketCategoryDetailVo;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Date;
import java.util.HexFormat;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolPurchaseIntentTest {

    private static final Instant NOW = Instant.parse("2026-09-27T08:00:00Z");
    private static final String QUOTE_HASH = "a".repeat(64);
    private static final String SECRET = "purchase-confirmation-contract-key-32";

    @Test
    void prepareUsesLiveJavaPriceAndPersistsFenSnapshot() {
        Fixture fixture = new Fixture();
        when(fixture.intentMapper.selectByIdempotency("tenant-1", "user-1", 1001L, "turn-1"))
                .thenReturn(null);
        when(fixture.ticketService.selectListByProgram(any()))
                .thenReturn(List.of(ticket("580.25", 9L)));
        when(fixture.uidGenerator.getUid()).thenReturn(7001L);
        when(fixture.intentMapper.insertIdempotent(any())).thenReturn(1);

        AgentPurchaseIntentVo result = fixture.service.prepare(
                "tenant-1", "user-1", "session-1", "turn-1", prepareRequest(2));

        ArgumentCaptor<PurchaseIntent> captured = ArgumentCaptor.forClass(PurchaseIntent.class);
        verify(fixture.intentMapper).insertIdempotent(captured.capture());
        PurchaseIntent persisted = captured.getValue();
        assertEquals(58025L, persisted.getUnitAmountFen());
        assertEquals(116050L, persisted.getTotalAmountFen());
        assertEquals(PurchaseIntentState.PENDING_CONFIRMATION.name(), persisted.getIntentState());
        assertEquals(64, persisted.getQuoteHash().length());
        assertEquals(116050L, result.getTotalAmountFen());
    }

    @Test
    void prepareRejectsInsufficientLiveInventory() {
        Fixture fixture = new Fixture();
        when(fixture.ticketService.selectListByProgram(any()))
                .thenReturn(List.of(ticket("580.00", 1L)));

        PurchaseIntentException error = assertThrows(
                PurchaseIntentException.class,
                () -> fixture.service.prepare(
                        "tenant-1", "user-1", "session-1", "turn-1", prepareRequest(2)));

        assertEquals(409, error.getCode());
        verify(fixture.intentMapper, never()).insertIdempotent(any());
    }

    @Test
    void sameIdempotencyKeyCannotChangeIntentParameters() {
        Fixture fixture = new Fixture();
        PurchaseIntent existing = pendingIntent();
        existing.setQuantity(1);
        when(fixture.intentMapper.selectByIdempotency("tenant-1", "user-1", 1001L, "turn-1"))
                .thenReturn(existing);

        PurchaseIntentException error = assertThrows(
                PurchaseIntentException.class,
                () -> fixture.service.prepare(
                        "tenant-1", "user-1", "session-1", "turn-1", prepareRequest(2)));

        assertEquals(409, error.getCode());
        verify(fixture.ticketService, never()).selectListByProgram(any());
    }

    @Test
    void cancelUsesOwnerShardAndOptimisticVersion() {
        Fixture fixture = new Fixture();
        PurchaseIntent pending = pendingIntent();
        PurchaseIntent cancelled = pendingIntent();
        cancelled.setIntentState(PurchaseIntentState.CANCELLED.name());
        cancelled.setVersion(2L);
        when(fixture.intentMapper.selectOwned("tenant-1", "user-1", 7001L, 1001L))
                .thenReturn(pending, cancelled);
        when(fixture.intentMapper.cancel(
                        eq("tenant-1"),
                        eq("user-1"),
                        eq(7001L),
                        eq(1001L),
                        eq(1L),
                        any()))
                .thenReturn(1);
        AgentPurchaseIntentCancelRequest request = new AgentPurchaseIntentCancelRequest();
        request.setIntentId(7001L);
        request.setProgramId(1001L);
        request.setExpectedVersion(1L);

        AgentPurchaseIntentVo result = fixture.service.cancel("tenant-1", "user-1", request);

        assertEquals(PurchaseIntentState.CANCELLED.name(), result.getIntentStatus());
        assertEquals(2L, result.getVersion());
    }

    @Test
    void confirmationRechecksPriceAndCreatesServerSideGrantAtomically() {
        Fixture fixture = new Fixture();
        PurchaseIntent pending = pendingIntent();
        PurchaseIntent confirmed = pendingIntent();
        confirmed.setIntentState(PurchaseIntentState.CONFIRMED.name());
        confirmed.setVersion(2L);
        confirmed.setConfirmedAt(Date.from(NOW));
        when(fixture.grantMapper.selectByProof(7001L, 1001L, "proof-1")).thenReturn(null);
        when(fixture.grantMapper.selectByNonce(1001L, "nonce-1")).thenReturn(null);
        when(fixture.intentMapper.selectOwned("tenant-1", "user-1", 7001L, 1001L))
                .thenReturn(pending, confirmed);
        when(fixture.ticketService.selectListByProgram(any()))
                .thenReturn(List.of(ticket("580.00", 8L)));
        when(fixture.intentMapper.confirm(
                        eq("tenant-1"),
                        eq("user-1"),
                        eq("session-1"),
                        eq(7001L),
                        eq(1001L),
                        eq(1L),
                        eq(QUOTE_HASH),
                        any()))
                .thenReturn(1);
        when(fixture.uidGenerator.getUid()).thenReturn(8001L);
        when(fixture.grantMapper.insertGrant(any())).thenReturn(1);

        AgentPurchaseIntentVo result = fixture.service.confirm(
                "tenant-1",
                "user-1",
                "session-1",
                7001L,
                1001L,
                1L,
                QUOTE_HASH,
                new ConfirmationProof("proof-1", "nonce-1", Date.from(NOW)));

        ArgumentCaptor<ConfirmationGrant> captured = ArgumentCaptor.forClass(ConfirmationGrant.class);
        verify(fixture.grantMapper).insertGrant(captured.capture());
        assertEquals(ConfirmationGrantState.AVAILABLE.name(), captured.getValue().getGrantState());
        assertEquals("nonce-1", captured.getValue().getNonceHash());
        assertEquals(2L, captured.getValue().getIntentVersion());
        assertEquals(PurchaseIntentState.CONFIRMED.name(), result.getIntentStatus());
    }

    @Test
    void confirmationRejectsChangedLivePriceBeforeStateMutation() {
        Fixture fixture = new Fixture();
        when(fixture.grantMapper.selectByProof(7001L, 1001L, "proof-1")).thenReturn(null);
        when(fixture.grantMapper.selectByNonce(1001L, "nonce-1")).thenReturn(null);
        when(fixture.intentMapper.selectOwned("tenant-1", "user-1", 7001L, 1001L))
                .thenReturn(pendingIntent());
        when(fixture.ticketService.selectListByProgram(any()))
                .thenReturn(List.of(ticket("680.00", 8L)));

        PurchaseIntentException error = assertThrows(
                PurchaseIntentException.class,
                () -> fixture.service.confirm(
                        "tenant-1",
                        "user-1",
                        "session-1",
                        7001L,
                        1001L,
                        1L,
                        QUOTE_HASH,
                        new ConfirmationProof("proof-1", "nonce-1", Date.from(NOW))));

        assertEquals(409, error.getCode());
        verify(fixture.intentMapper, never()).confirm(
                any(), any(), any(), any(), any(), any(), any(), any());
        verify(fixture.grantMapper, never()).insertGrant(any());
    }

    @Test
    void confirmationRejectsNonceReplayBeforeReadingIntent() {
        Fixture fixture = new Fixture();
        when(fixture.grantMapper.selectByProof(7001L, 1001L, "proof-2")).thenReturn(null);
        when(fixture.grantMapper.selectByNonce(1001L, "nonce-used"))
                .thenReturn(new ConfirmationGrant());

        PurchaseIntentException error = assertThrows(
                PurchaseIntentException.class,
                () -> fixture.service.confirm(
                        "tenant-1",
                        "user-1",
                        "session-1",
                        7001L,
                        1001L,
                        1L,
                        QUOTE_HASH,
                        new ConfirmationProof(
                                "proof-2", "nonce-used", Date.from(NOW))));

        assertEquals(409, error.getCode());
        verify(fixture.intentMapper, never()).selectOwned(any(), any(), any(), any());
    }

    @Test
    void confirmationProofIsBoundAndTimeLimited() throws Exception {
        Clock clock = Clock.fixed(NOW, ZoneOffset.UTC);
        ConfirmationProofVerifier verifier = new ConfirmationProofVerifier(
                SECRET, Duration.ofSeconds(120), clock);
        AgentPurchaseConfirmationRequest request = confirmationRequest(NOW);
        String signature = sign(request);

        ConfirmationProof proof = verifier.verify(
                "tenant-1", "user-1", "session-1", request, signature);

        assertEquals(64, proof.proofHash().length());
        assertEquals(Date.from(NOW), proof.confirmedAt());
        assertThrows(
                PurchaseIntentException.class,
                () -> verifier.verify(
                        "another-tenant", "user-1", "session-1", request, signature));

        AgentPurchaseConfirmationRequest stale = confirmationRequest(NOW.minusSeconds(121));
        assertThrows(
                PurchaseIntentException.class,
                () -> verifier.verify(
                        "tenant-1", "user-1", "session-1", stale, sign(stale)));
    }

    @Test
    void enabledSecurityRejectsWeakOrSharedConfirmationKeys() {
        assertThrows(
                IllegalStateException.class,
                () -> new PurchaseIntentSecurityConfiguration("weak", "d".repeat(32)));
        assertThrows(
                IllegalStateException.class,
                () -> new PurchaseIntentSecurityConfiguration(
                        "s".repeat(32), "s".repeat(32)));
        new PurchaseIntentSecurityConfiguration("c".repeat(32), "d".repeat(32));
    }

    private AgentPurchaseIntentPrepareRequest prepareRequest(int quantity) {
        AgentPurchaseIntentPrepareRequest request = new AgentPurchaseIntentPrepareRequest();
        request.setProgramId(1001L);
        request.setTicketCategoryId(3001L);
        request.setQuantity(quantity);
        return request;
    }

    private static AgentPurchaseConfirmationRequest confirmationRequest(Instant confirmedAt) {
        AgentPurchaseConfirmationRequest request = new AgentPurchaseConfirmationRequest();
        request.setIntentId(7001L);
        request.setProgramId(1001L);
        request.setExpectedVersion(1L);
        request.setQuoteHash(QUOTE_HASH);
        request.setConfirmationNonce("nonce-0123456789abcdef");
        request.setConfirmedAt(confirmedAt.toString());
        return request;
    }

    private static String sign(AgentPurchaseConfirmationRequest request) throws Exception {
        String canonical = PurchaseQuoteFingerprint.canonicalProof(
                "tenant-1",
                "user-1",
                "session-1",
                request.getIntentId(),
                request.getProgramId(),
                request.getExpectedVersion(),
                request.getQuoteHash(),
                Instant.parse(request.getConfirmedAt()),
                request.getConfirmationNonce());
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return HexFormat.of().formatHex(mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8)));
    }

    private static PurchaseIntent pendingIntent() {
        PurchaseIntent intent = new PurchaseIntent();
        intent.setId(7001L);
        intent.setTenantId("tenant-1");
        intent.setUserId("user-1");
        intent.setSessionKey("session-1");
        intent.setIdempotencyKey("turn-1");
        intent.setProgramId(1001L);
        intent.setTicketCategoryId(3001L);
        intent.setQuantity(2);
        intent.setUnitAmountFen(58000L);
        intent.setTotalAmountFen(116000L);
        intent.setCurrency("CNY");
        intent.setQuoteHash(QUOTE_HASH);
        intent.setQuoteExpiresAt(Date.from(NOW.plusSeconds(120)));
        intent.setIntentState(PurchaseIntentState.PENDING_CONFIRMATION.name());
        intent.setVersion(1L);
        intent.setCreateTime(Date.from(NOW.minusSeconds(5)));
        intent.setEditTime(Date.from(NOW.minusSeconds(5)));
        return intent;
    }

    private static TicketCategoryDetailVo ticket(String price, long remaining) {
        TicketCategoryDetailVo ticket = new TicketCategoryDetailVo();
        ticket.setId(3001L);
        ticket.setProgramId(1001L);
        ticket.setPrice(new BigDecimal(price));
        ticket.setRemainNumber(remaining);
        return ticket;
    }

    private static final class Fixture {
        private final PurchaseIntentMapper intentMapper = mock(PurchaseIntentMapper.class);
        private final ConfirmationGrantMapper grantMapper = mock(ConfirmationGrantMapper.class);
        private final TicketCategoryService ticketService = mock(TicketCategoryService.class);
        private final UidGenerator uidGenerator = mock(UidGenerator.class);
        private final PurchaseIntentService service = new PurchaseIntentService(
                intentMapper,
                grantMapper,
                ticketService,
                uidGenerator,
                Duration.ofSeconds(120),
                Duration.ofSeconds(90),
                Clock.fixed(NOW, ZoneOffset.UTC));
    }
}
