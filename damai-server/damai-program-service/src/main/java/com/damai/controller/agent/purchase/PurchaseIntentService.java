package com.damai.controller.agent.purchase;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.dto.AgentPurchaseIntentCancelRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentGetRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentPrepareRequest;
import com.damai.controller.agent.vo.AgentPurchaseIntentVo;
import com.damai.dto.TicketCategoryListByProgramDto;
import com.damai.service.TicketCategoryService;
import com.damai.vo.TicketCategoryDetailVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.RoundingMode;
import java.time.Clock;
import java.time.Duration;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Date;
import java.util.List;

/** Java-authoritative purchase intent, quote snapshot, and confirmation grant service. */
@Service
public class PurchaseIntentService {

    private static final String CURRENCY = "CNY";

    private final PurchaseIntentMapper intentMapper;
    private final ConfirmationGrantMapper grantMapper;
    private final TicketCategoryService ticketCategoryService;
    private final UidGenerator uidGenerator;
    private final Duration quoteTtl;
    private final Duration grantTtl;
    private final Clock clock;

    @Autowired
    public PurchaseIntentService(
            PurchaseIntentMapper intentMapper,
            ConfirmationGrantMapper grantMapper,
            TicketCategoryService ticketCategoryService,
            UidGenerator uidGenerator,
            @Value("${agent.purchase-intents.quote-ttl-seconds:120}") long quoteTtlSeconds,
            @Value("${agent.purchase-intents.grant-ttl-seconds:90}") long grantTtlSeconds) {
        this(
                intentMapper,
                grantMapper,
                ticketCategoryService,
                uidGenerator,
                Duration.ofSeconds(Math.max(30, quoteTtlSeconds)),
                Duration.ofSeconds(Math.max(15, grantTtlSeconds)),
                Clock.systemUTC());
    }

    PurchaseIntentService(
            PurchaseIntentMapper intentMapper,
            ConfirmationGrantMapper grantMapper,
            TicketCategoryService ticketCategoryService,
            UidGenerator uidGenerator,
            Duration quoteTtl,
            Duration grantTtl) {
        this(
                intentMapper,
                grantMapper,
                ticketCategoryService,
                uidGenerator,
                quoteTtl,
                grantTtl,
                Clock.systemUTC());
    }

    PurchaseIntentService(
            PurchaseIntentMapper intentMapper,
            ConfirmationGrantMapper grantMapper,
            TicketCategoryService ticketCategoryService,
            UidGenerator uidGenerator,
            Duration quoteTtl,
            Duration grantTtl,
            Clock clock) {
        this.intentMapper = intentMapper;
        this.grantMapper = grantMapper;
        this.ticketCategoryService = ticketCategoryService;
        this.uidGenerator = uidGenerator;
        this.quoteTtl = quoteTtl;
        this.grantTtl = grantTtl;
        this.clock = clock;
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentPurchaseIntentVo prepare(
            String tenantId,
            String userId,
            String sessionKey,
            String idempotencyKey,
            AgentPurchaseIntentPrepareRequest request) {
        PurchaseIntent existing = intentMapper.selectByIdempotency(
                tenantId, userId, request.getProgramId(), idempotencyKey);
        if (existing != null) {
            requireSameRequest(existing, sessionKey, request);
            return toVo(existing, Date.from(clock.instant()));
        }

        TicketCategoryDetailVo ticket = requireAvailableTicket(
                request.getProgramId(), request.getTicketCategoryId(), request.getQuantity());
        long unitAmountFen = toFen(ticket);
        long totalAmountFen;
        try {
            totalAmountFen = Math.multiplyExact(unitAmountFen, request.getQuantity().longValue());
        } catch (ArithmeticException exception) {
            throw new PurchaseIntentException(422, "票价金额超出允许范围");
        }
        Date now = Date.from(clock.instant());
        PurchaseIntent intent = new PurchaseIntent();
        intent.setId(uidGenerator.getUid());
        intent.setTenantId(tenantId);
        intent.setUserId(userId);
        intent.setSessionKey(sessionKey);
        intent.setIdempotencyKey(idempotencyKey);
        intent.setProgramId(request.getProgramId());
        intent.setTicketCategoryId(request.getTicketCategoryId());
        intent.setQuantity(request.getQuantity());
        intent.setUnitAmountFen(unitAmountFen);
        intent.setTotalAmountFen(totalAmountFen);
        intent.setCurrency(CURRENCY);
        intent.setQuoteExpiresAt(Date.from(now.toInstant().plus(quoteTtl)));
        intent.setIntentState(PurchaseIntentState.PENDING_CONFIRMATION.name());
        intent.setVersion(1L);
        intent.setCreateTime(now);
        intent.setEditTime(now);
        intent.setQuoteHash(PurchaseQuoteFingerprint.create(intent));

        if (intentMapper.insertIdempotent(intent) == 1) {
            return toVo(intent, now);
        }
        PurchaseIntent winner = intentMapper.selectByIdempotency(
                tenantId, userId, request.getProgramId(), idempotencyKey);
        if (winner == null) {
            throw new PurchaseIntentException(409, "购买意向幂等创建冲突，请重新查询");
        }
        requireSameRequest(winner, sessionKey, request);
        return toVo(winner, now);
    }

    public AgentPurchaseIntentVo get(
            String tenantId, String userId, AgentPurchaseIntentGetRequest request) {
        return toVo(
                requireOwned(tenantId, userId, request.getIntentId(), request.getProgramId()),
                Date.from(clock.instant()));
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentPurchaseIntentVo cancel(
            String tenantId, String userId, AgentPurchaseIntentCancelRequest request) {
        PurchaseIntent current = requireOwned(
                tenantId, userId, request.getIntentId(), request.getProgramId());
        if (PurchaseIntentState.CANCELLED.name().equals(current.getIntentState())) {
            return toVo(current, Date.from(clock.instant()));
        }
        requireVersion(current, request.getExpectedVersion());
        if (!PurchaseIntentState.PENDING_CONFIRMATION.name().equals(current.getIntentState())) {
            throw new PurchaseIntentException(409, "当前购买意向状态不可取消");
        }
        Date now = Date.from(clock.instant());
        if (intentMapper.cancel(
                        tenantId,
                        userId,
                        request.getIntentId(),
                        request.getProgramId(),
                        request.getExpectedVersion(),
                        now)
                != 1) {
            throw new PurchaseIntentException(409, "购买意向版本冲突，请重新查询");
        }
        return toVo(requireOwned(
                tenantId, userId, request.getIntentId(), request.getProgramId()), now);
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentPurchaseIntentVo confirm(
            String tenantId,
            String userId,
            String sessionKey,
            Long intentId,
            Long programId,
            Long expectedVersion,
            String quoteHash,
            ConfirmationProof proof) {
        ConfirmationGrant replay = grantMapper.selectByProof(
                intentId, programId, proof.proofHash());
        if (replay != null) {
            requireGrantOwner(replay, tenantId, userId, sessionKey, quoteHash);
            return toVo(requireOwned(tenantId, userId, intentId, programId), proof.confirmedAt());
        }
        if (grantMapper.selectByNonce(programId, proof.nonceHash()) != null) {
            throw new PurchaseIntentException(409, "购买确认 nonce 已使用");
        }

        PurchaseIntent intent = requireOwned(tenantId, userId, intentId, programId);
        requireVersion(intent, expectedVersion);
        Date now = Date.from(clock.instant());
        if (!PurchaseIntentState.PENDING_CONFIRMATION.name().equals(intent.getIntentState())
                || !intent.getSessionKey().equals(sessionKey)
                || !PurchaseQuoteFingerprint.equals(intent.getQuoteHash(), quoteHash)
                || !proof.confirmedAt().before(intent.getQuoteExpiresAt())
                || !now.before(intent.getQuoteExpiresAt())
                || !now.toInstant().isBefore(proof.confirmedAt().toInstant().plus(grantTtl))) {
            throw new PurchaseIntentException(409, "购买意向或报价已失效，请重新准备");
        }
        TicketCategoryDetailVo currentTicket = requireAvailableTicket(
                intent.getProgramId(), intent.getTicketCategoryId(), intent.getQuantity());
        if (toFen(currentTicket) != intent.getUnitAmountFen()) {
            throw new PurchaseIntentException(409, "实时票价已变化，请重新准备购买意向");
        }

        if (intentMapper.confirm(
                        tenantId,
                        userId,
                        sessionKey,
                        intentId,
                        programId,
                        expectedVersion,
                        quoteHash,
                        proof.confirmedAt())
                != 1) {
            throw new PurchaseIntentException(409, "购买意向确认冲突，请重新查询");
        }
        ConfirmationGrant grant = new ConfirmationGrant();
        grant.setId(uidGenerator.getUid());
        grant.setIntentId(intentId);
        grant.setProgramId(programId);
        grant.setIntentVersion(expectedVersion + 1);
        grant.setTenantId(tenantId);
        grant.setUserId(userId);
        grant.setSessionKey(sessionKey);
        grant.setQuoteHash(quoteHash);
        grant.setProofHash(proof.proofHash());
        grant.setNonceHash(proof.nonceHash());
        grant.setGrantState(ConfirmationGrantState.AVAILABLE.name());
        Date grantExpiry = Date.from(proof.confirmedAt().toInstant().plus(grantTtl));
        grant.setExpiresAt(grantExpiry.before(intent.getQuoteExpiresAt())
                ? grantExpiry
                : intent.getQuoteExpiresAt());
        grant.setCreateTime(proof.confirmedAt());
        grant.setEditTime(proof.confirmedAt());
        try {
            if (grantMapper.insertGrant(grant) != 1) {
                throw new IllegalStateException("confirmation grant was not persisted");
            }
        } catch (DuplicateKeyException exception) {
            throw new PurchaseIntentException(409, "购买确认已被并发处理");
        }
        return toVo(requireOwned(tenantId, userId, intentId, programId), proof.confirmedAt());
    }

    private TicketCategoryDetailVo requireAvailableTicket(
            Long programId, Long ticketCategoryId, int quantity) {
        TicketCategoryListByProgramDto query = new TicketCategoryListByProgramDto();
        query.setProgramId(programId);
        List<TicketCategoryDetailVo> tickets = ticketCategoryService.selectListByProgram(query);
        TicketCategoryDetailVo ticket = tickets == null
                ? null
                : tickets.stream()
                        .filter(item -> item != null
                                && ticketCategoryId.equals(item.getId())
                                && programId.equals(item.getProgramId()))
                        .findFirst()
                        .orElse(null);
        if (ticket == null || ticket.getPrice() == null || ticket.getRemainNumber() == null) {
            throw new PurchaseIntentException(404, "节目票档不存在或暂不可售");
        }
        if (ticket.getRemainNumber() < quantity) {
            throw new PurchaseIntentException(409, "实时余票不足，请调整数量");
        }
        return ticket;
    }

    private long toFen(TicketCategoryDetailVo ticket) {
        try {
            return ticket.getPrice()
                    .movePointRight(2)
                    .setScale(0, RoundingMode.UNNECESSARY)
                    .longValueExact();
        } catch (ArithmeticException exception) {
            throw new PurchaseIntentException(422, "票价精度不符合人民币分格式");
        }
    }

    private PurchaseIntent requireOwned(
            String tenantId, String userId, Long intentId, Long programId) {
        PurchaseIntent intent = intentMapper.selectOwned(tenantId, userId, intentId, programId);
        if (intent == null) {
            throw new PurchaseIntentException(404, "购买意向不存在");
        }
        return intent;
    }

    private void requireSameRequest(
            PurchaseIntent existing,
            String sessionKey,
            AgentPurchaseIntentPrepareRequest request) {
        if (!existing.getSessionKey().equals(sessionKey)
                || !existing.getTicketCategoryId().equals(request.getTicketCategoryId())
                || !existing.getQuantity().equals(request.getQuantity())) {
            throw new PurchaseIntentException(409, "同一幂等键不能变更购买意向参数");
        }
    }

    private void requireVersion(PurchaseIntent intent, Long expectedVersion) {
        if (!intent.getVersion().equals(expectedVersion)) {
            throw new PurchaseIntentException(409, "购买意向版本冲突，请重新查询");
        }
    }

    private void requireGrantOwner(
            ConfirmationGrant grant,
            String tenantId,
            String userId,
            String sessionKey,
            String quoteHash) {
        if (!grant.getTenantId().equals(tenantId)
                || !grant.getUserId().equals(userId)
                || !grant.getSessionKey().equals(sessionKey)
                || !PurchaseQuoteFingerprint.equals(grant.getQuoteHash(), quoteHash)) {
            throw new PurchaseIntentException(401, "购买确认凭据归属不匹配");
        }
    }

    private AgentPurchaseIntentVo toVo(PurchaseIntent intent, Date now) {
        AgentPurchaseIntentVo view = new AgentPurchaseIntentVo();
        view.setIntentId(intent.getId());
        view.setProgramId(intent.getProgramId());
        view.setTicketCategoryId(intent.getTicketCategoryId());
        view.setQuantity(intent.getQuantity());
        view.setUnitAmountFen(intent.getUnitAmountFen());
        view.setTotalAmountFen(intent.getTotalAmountFen());
        view.setCurrency(intent.getCurrency());
        view.setQuoteHash(intent.getQuoteHash());
        view.setQuoteExpiresAt(format(intent.getQuoteExpiresAt()));
        view.setIntentStatus(effectiveState(intent, now));
        view.setVersion(intent.getVersion());
        view.setCreatedAt(format(intent.getCreateTime()));
        view.setUpdatedAt(format(intent.getEditTime()));
        return view;
    }

    private String effectiveState(PurchaseIntent intent, Date now) {
        if (PurchaseIntentState.PENDING_CONFIRMATION.name().equals(intent.getIntentState())
                && !now.before(intent.getQuoteExpiresAt())) {
            return PurchaseIntentState.EXPIRED.name();
        }
        return intent.getIntentState();
    }

    private String format(Date value) {
        return DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(
                value.toInstant().atOffset(ZoneOffset.UTC));
    }
}
