package com.damai.controller.agent.purchase;

import com.baidu.fsg.uid.UidGenerator;
import com.damai.controller.agent.dto.AgentPurchaseOrderSubmitRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

import static com.damai.service.constant.ProgramOrderConstant.ORDER_TABLE_COUNT;

/** Transactional grant consumption, durable command creation, and fenced recovery updates. */
@Service
public class PurchaseOrderSubmissionStore {

    private final PurchaseIntentMapper intentMapper;
    private final ConfirmationGrantMapper grantMapper;
    private final PurchaseOrderSubmissionMapper submissionMapper;
    private final UidGenerator uidGenerator;
    private final Clock clock;

    public PurchaseOrderSubmissionStore(
            PurchaseIntentMapper intentMapper,
            ConfirmationGrantMapper grantMapper,
            PurchaseOrderSubmissionMapper submissionMapper,
            UidGenerator uidGenerator) {
        this(intentMapper, grantMapper, submissionMapper, uidGenerator, Clock.systemUTC());
    }

    PurchaseOrderSubmissionStore(
            PurchaseIntentMapper intentMapper,
            ConfirmationGrantMapper grantMapper,
            PurchaseOrderSubmissionMapper submissionMapper,
            UidGenerator uidGenerator,
            Clock clock) {
        this.intentMapper = intentMapper;
        this.grantMapper = grantMapper;
        this.submissionMapper = submissionMapper;
        this.uidGenerator = uidGenerator;
        this.clock = clock;
    }

    @Transactional(rollbackFor = Exception.class)
    public PurchaseOrderSubmission begin(
            String tenantId,
            String userId,
            String sessionKey,
            AgentPurchaseOrderSubmitRequest request) {
        PurchaseIntent intent = intentMapper.selectOwnedForUpdate(
                tenantId, userId, request.getIntentId(), request.getProgramId());
        if (intent == null) {
            throw new PurchaseIntentException(404, "购买意向不存在");
        }
        PurchaseOrderSubmission existing = submissionMapper.selectByIntent(
                request.getIntentId(), request.getProgramId());
        if (existing != null) {
            requireOwner(existing, tenantId, userId, sessionKey);
            return existing;
        }
        if (!PurchaseIntentState.CONFIRMED.name().equals(intent.getIntentState())
                || !Objects.equals(intent.getVersion(), request.getExpectedVersion())
                || !Objects.equals(intent.getSessionKey(), sessionKey)) {
            throw new PurchaseIntentException(409, "购买意向尚未确认或版本已变化");
        }
        List<Long> ticketUserIds = PurchaseTicketUserRefs.parse(intent.getTicketUserRefs());
        if (ticketUserIds.size() != intent.getQuantity()) {
            throw new PurchaseIntentException(409, "购买意向购票人引用与数量不一致");
        }
        ConfirmationGrant grant = grantMapper.selectByIntentForUpdate(
                request.getIntentId(), request.getProgramId());
        Date now = Date.from(clock.instant());
        if (grant == null
                || !ConfirmationGrantState.AVAILABLE.name().equals(grant.getGrantState())
                || !grant.getExpiresAt().after(now)
                || !Objects.equals(grant.getIntentVersion(), request.getExpectedVersion())
                || !Objects.equals(grant.getTenantId(), tenantId)
                || !Objects.equals(grant.getUserId(), userId)
                || !Objects.equals(grant.getSessionKey(), sessionKey)
                || !PurchaseQuoteFingerprint.equals(grant.getQuoteHash(), intent.getQuoteHash())) {
            throw new PurchaseIntentException(409, "一次性购买确认授权不存在或已失效");
        }

        long numericUserId = numericUserId(userId);
        PurchaseOrderSubmission submission = new PurchaseOrderSubmission();
        submission.setId(uidGenerator.getUid());
        submission.setIntentId(intent.getId());
        submission.setProgramId(intent.getProgramId());
        submission.setIntentVersion(intent.getVersion() + 1);
        submission.setTenantId(tenantId);
        submission.setUserId(userId);
        submission.setSessionKey(sessionKey);
        submission.setTicketCategoryId(intent.getTicketCategoryId());
        submission.setQuantity(intent.getQuantity());
        submission.setTicketUserRefs(intent.getTicketUserRefs());
        submission.setUnitAmountFen(intent.getUnitAmountFen());
        submission.setTotalAmountFen(intent.getTotalAmountFen());
        submission.setCurrency(intent.getCurrency());
        submission.setOrderNumber(uidGenerator.getOrderNumber(numericUserId, ORDER_TABLE_COUNT));
        submission.setSubmissionState(OrderSubmissionState.PENDING.name());
        submission.setAttemptCount(0);
        submission.setNextAttemptTime(now);
        submission.setCreateTime(now);
        submission.setEditTime(now);

        if (submissionMapper.insertSubmission(submission) != 1
                || grantMapper.consume(grant.getId(), intent.getId(), intent.getProgramId(), now) != 1
                || intentMapper.startSubmission(
                                tenantId,
                                userId,
                                sessionKey,
                                intent.getId(),
                                intent.getProgramId(),
                                request.getExpectedVersion(),
                                submission.getOrderNumber(),
                                now)
                        != 1) {
            throw new PurchaseIntentException(409, "购买确认授权并发消费冲突");
        }
        return submission;
    }

    public PurchaseOrderSubmission selectOwned(
            String tenantId, String userId, Long intentId, Long programId) {
        PurchaseOrderSubmission submission = submissionMapper.selectOwned(
                tenantId, userId, intentId, programId);
        if (submission == null) {
            throw new PurchaseIntentException(404, "订单提交记录不存在");
        }
        return submission;
    }

    public PurchaseOrderSubmission claimOne(
            PurchaseOrderSubmission candidate, String owner, Duration leaseDuration) {
        Date now = Date.from(clock.instant());
        return claim(candidate, now, owner, leaseDuration);
    }

    public List<PurchaseOrderSubmission> claimDue(
            Date now,
            String owner,
            Duration leaseDuration,
            int partitions,
            int limit) {
        int safePartitions = Math.max(1, partitions);
        int safeLimit = Math.max(1, limit);
        List<PurchaseOrderSubmission> claimed = new ArrayList<>();
        for (int partition = 0; partition < safePartitions && claimed.size() < safeLimit; partition++) {
            List<PurchaseOrderSubmission> candidates = submissionMapper.selectDueCandidates(
                    now, partition, safePartitions, safeLimit - claimed.size());
            for (PurchaseOrderSubmission candidate : candidates) {
                PurchaseOrderSubmission accepted = claim(candidate, now, owner, leaseDuration);
                if (accepted != null) {
                    claimed.add(accepted);
                }
            }
        }
        return claimed;
    }

    private PurchaseOrderSubmission claim(
            PurchaseOrderSubmission candidate,
            Date now,
            String owner,
            Duration leaseDuration) {
        if (candidate == null) {
            return null;
        }
        boolean reclaimedUnknown = OrderSubmissionState.PROCESSING.name()
                        .equals(candidate.getSubmissionState())
                || OrderSubmissionState.RECONCILE.name()
                        .equals(candidate.getSubmissionState());
        String token = UUID.randomUUID().toString();
        Date expiresAt = Date.from(now.toInstant().plus(leaseDuration));
        if (submissionMapper.tryClaim(
                        candidate.getId(),
                        candidate.getProgramId(),
                        now,
                        owner,
                        token,
                        expiresAt)
                != 1) {
            return null;
        }
        candidate.setSubmissionState(OrderSubmissionState.PROCESSING.name());
        candidate.setAttemptCount(candidate.getAttemptCount() + 1);
        candidate.setLeaseOwner(owner);
        candidate.setLeaseToken(token);
        candidate.setLeaseExpiresAt(expiresAt);
        candidate.setReclaimedUnknown(reclaimedUnknown);
        return candidate;
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean markSubmitted(PurchaseOrderSubmission submission, Date submittedAt) {
        if (submissionMapper.markSubmitted(
                        submission.getId(),
                        submission.getProgramId(),
                        submission.getLeaseOwner(),
                        submission.getLeaseToken(),
                        submittedAt)
                != 1) {
            return false;
        }
        if (intentMapper.markSubmitted(
                        submission.getIntentId(),
                        submission.getProgramId(),
                        submission.getOrderNumber(),
                        submittedAt)
                != 1) {
            throw new IllegalStateException("purchase intent submitted transition was fenced");
        }
        return true;
    }

    public boolean markRetry(
            PurchaseOrderSubmission submission,
            Date failedAt,
            Date nextAttemptTime,
            String errorCode) {
        return submissionMapper.markRetry(
                        submission.getId(),
                        submission.getProgramId(),
                        submission.getLeaseOwner(),
                        submission.getLeaseToken(),
                        nextAttemptTime,
                        boundedCode(errorCode),
                        failedAt)
                == 1;
    }

    public boolean markReconcile(
            PurchaseOrderSubmission submission,
            Date failedAt,
            Date nextAttemptTime,
            String errorCode) {
        return submissionMapper.markReconcile(
                        submission.getId(),
                        submission.getProgramId(),
                        submission.getLeaseOwner(),
                        submission.getLeaseToken(),
                        nextAttemptTime,
                        boundedCode(errorCode),
                        failedAt)
                == 1;
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean markTerminal(
            PurchaseOrderSubmission submission,
            Date failedAt,
            boolean unknown,
            String errorCode) {
        String nextState = unknown
                ? OrderSubmissionState.MANUAL_REVIEW.name()
                : OrderSubmissionState.FAILED.name();
        if (submissionMapper.markTerminal(
                        submission.getId(),
                        submission.getProgramId(),
                        submission.getLeaseOwner(),
                        submission.getLeaseToken(),
                        nextState,
                        boundedCode(errorCode),
                        failedAt)
                != 1) {
            return false;
        }
        String intentState = unknown
                ? PurchaseIntentState.SUBMISSION_UNKNOWN.name()
                : PurchaseIntentState.SUBMISSION_FAILED.name();
        if (intentMapper.markSubmissionTerminal(
                        submission.getIntentId(),
                        submission.getProgramId(),
                        submission.getOrderNumber(),
                        intentState,
                        failedAt)
                != 1) {
            throw new IllegalStateException("purchase intent terminal transition was fenced");
        }
        return true;
    }

    public long countDue(Date now) {
        return submissionMapper.countDue(now);
    }

    private void requireOwner(
            PurchaseOrderSubmission submission,
            String tenantId,
            String userId,
            String sessionKey) {
        if (!Objects.equals(submission.getTenantId(), tenantId)
                || !Objects.equals(submission.getUserId(), userId)
                || !Objects.equals(submission.getSessionKey(), sessionKey)) {
            throw new PurchaseIntentException(401, "订单提交记录归属不匹配");
        }
    }

    private long numericUserId(String userId) {
        try {
            long value = Long.parseLong(userId);
            if (value <= 0) {
                throw new NumberFormatException("non-positive");
            }
            return value;
        } catch (NumberFormatException exception) {
            throw new PurchaseIntentException(422, "用户标识不能映射到购票账户");
        }
    }

    private String boundedCode(String value) {
        String normalized = value == null || value.isBlank() ? "UNKNOWN" : value.trim();
        return normalized.length() <= 64 ? normalized : normalized.substring(0, 64);
    }
}
