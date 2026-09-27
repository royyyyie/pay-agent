package com.damai.controller.agent.watch;

import com.baidu.fsg.uid.UidGenerator;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.UUID;

/** Database-backed fencing leases and atomic execution persistence. */
@Service
public class WatchRuleLeaseService {

    private final WatchRuleMapper watchRuleMapper;
    private final WatchRuleExecutionMapper executionMapper;
    private final WatchNotificationOutboxMapper outboxMapper;
    private final UidGenerator uidGenerator;
    private final Duration notificationCooldown;
    private final boolean notificationEnabled;

    @Autowired
    public WatchRuleLeaseService(
            WatchRuleMapper watchRuleMapper,
            WatchRuleExecutionMapper executionMapper,
            WatchNotificationOutboxMapper outboxMapper,
            UidGenerator uidGenerator,
            @Value("${agent.watch-rules.notification-cooldown-seconds:900}")
                    long notificationCooldownSeconds,
            @Value("${agent.watch-rules.notification-enabled:false}")
                    boolean notificationEnabled) {
        this.watchRuleMapper = watchRuleMapper;
        this.executionMapper = executionMapper;
        this.outboxMapper = outboxMapper;
        this.uidGenerator = uidGenerator;
        this.notificationCooldown =
                Duration.ofSeconds(Math.max(30, notificationCooldownSeconds));
        this.notificationEnabled = notificationEnabled;
    }

    public WatchRuleLeaseService(
            WatchRuleMapper watchRuleMapper,
            WatchRuleExecutionMapper executionMapper,
            WatchNotificationOutboxMapper outboxMapper,
            UidGenerator uidGenerator,
            Duration notificationCooldown,
            boolean notificationEnabled) {
        this.watchRuleMapper = watchRuleMapper;
        this.executionMapper = executionMapper;
        this.outboxMapper = outboxMapper;
        this.uidGenerator = uidGenerator;
        this.notificationCooldown = notificationCooldown;
        this.notificationEnabled = notificationEnabled;
    }

    public List<WatchRule> claimDue(
            Date now,
            String leaseOwner,
            Duration leaseDuration,
            int partitionCount,
            int limit) {
        int safePartitions = Math.max(1, partitionCount);
        int safeLimit = Math.max(1, limit);
        Date expiresAt = Date.from(now.toInstant().plus(leaseDuration));
        List<WatchRule> claimed = new ArrayList<>();
        for (int partition = 0; partition < safePartitions && claimed.size() < safeLimit; partition++) {
            int remaining = safeLimit - claimed.size();
            List<WatchRule> candidates = watchRuleMapper.selectDueCandidates(
                    now, partition, safePartitions, remaining);
            for (WatchRule candidate : candidates) {
                String token = UUID.randomUUID().toString();
                int updated = watchRuleMapper.tryClaim(
                        candidate.getId(),
                        candidate.getProgramId(),
                        candidate.getVersion(),
                        now,
                        leaseOwner,
                        token,
                        expiresAt);
                if (updated == 1) {
                    candidate.setLeaseOwner(leaseOwner);
                    candidate.setLeaseToken(token);
                    candidate.setLeaseExpiresAt(expiresAt);
                    candidate.setClaimedVersion(candidate.getVersion());
                    claimed.add(candidate);
                }
            }
        }
        return claimed;
    }

    @Transactional(rollbackFor = Exception.class)
    public boolean complete(
            WatchRule claimedRule,
            WatchEvaluation evaluation,
            Date checkedAt,
            Date nextCheckTime) {
        WatchRuleExecution execution = toExecution(
                claimedRule, evaluation, checkedAt, nextCheckTime);
        WatchNotificationOutbox outbox = shouldNotify(claimedRule, evaluation, checkedAt)
                ? toOutbox(claimedRule, evaluation, execution.getId(), checkedAt)
                : null;
        int updated = watchRuleMapper.completeClaim(
                claimedRule.getId(),
                claimedRule.getProgramId(),
                claimedRule.getClaimedVersion(),
                claimedRule.getLeaseOwner(),
                claimedRule.getLeaseToken(),
                checkedAt,
                nextCheckTime,
                outbox == null ? null : checkedAt);
        if (updated != 1) {
            return false;
        }
        if (executionMapper.insertIdempotent(execution) != 1) {
            throw new IllegalStateException("watch execution was not persisted");
        }
        if (outbox != null) {
            outboxMapper.insertIdempotent(outbox);
        }
        return true;
    }

    @Transactional(rollbackFor = Exception.class)
    public void recordStale(WatchRule claimedRule, Date checkedAt) {
        executionMapper.insertIdempotent(toExecution(
                claimedRule, WatchEvaluation.stale(), checkedAt, null));
    }

    public long countDue(Date now) {
        return watchRuleMapper.countDue(now);
    }

    private WatchRuleExecution toExecution(
            WatchRule claimedRule,
            WatchEvaluation evaluation,
            Date checkedAt,
            Date nextCheckTime) {
        WatchRuleExecution execution = new WatchRuleExecution();
        execution.setId(uidGenerator.getUid());
        execution.setRuleId(claimedRule.getId());
        execution.setProgramId(claimedRule.getProgramId());
        execution.setRuleVersion(claimedRule.getClaimedVersion());
        execution.setLeaseToken(claimedRule.getLeaseToken());
        execution.setCheckedAt(checkedAt);
        execution.setOutcome(evaluation.outcome().name());
        execution.setMatchedTicketCategoryIds(evaluation.matchedTicketCategoryIds());
        execution.setMatchedCategoryCount(evaluation.matchedCategoryCount());
        execution.setMatchedRemaining(evaluation.matchedRemaining());
        execution.setMinimumPrice(evaluation.minimumPrice());
        execution.setErrorCode(evaluation.errorCode());
        execution.setNextCheckTime(nextCheckTime);
        execution.setCreateTime(checkedAt);
        return execution;
    }

    private boolean shouldNotify(
            WatchRule rule, WatchEvaluation evaluation, Date checkedAt) {
        if (!notificationEnabled
                || evaluation.outcome() != WatchCheckOutcome.MATCHED
                || evaluation.matchedCategoryCount() <= 0
                || evaluation.minimumPrice() == null
                || evaluation.matchedTicketCategoryIds() == null
                || evaluation.matchedTicketCategoryIds().isBlank()) {
            return false;
        }
        Date lastTriggered = rule.getLastTriggeredTime();
        return lastTriggered == null
                || !checkedAt.before(Date.from(
                        lastTriggered.toInstant().plus(notificationCooldown)));
    }

    private WatchNotificationOutbox toOutbox(
            WatchRule rule,
            WatchEvaluation evaluation,
            Long executionId,
            Date checkedAt) {
        String fingerprint = WatchNotificationFingerprint.condition(rule);
        Date windowStart = WatchNotificationFingerprint.windowStart(
                checkedAt, notificationCooldown);
        String dedupeKey = WatchNotificationFingerprint.dedupeKey(
                rule, fingerprint, windowStart);
        WatchNotificationOutbox outbox = new WatchNotificationOutbox();
        outbox.setId(uidGenerator.getUid());
        outbox.setEventId("watch-" + dedupeKey);
        outbox.setRuleId(rule.getId());
        outbox.setProgramId(rule.getProgramId());
        outbox.setRuleVersion(rule.getClaimedVersion());
        outbox.setExecutionId(executionId);
        outbox.setConditionFingerprint(fingerprint);
        outbox.setDedupeWindowStart(windowStart);
        outbox.setDedupeKey(dedupeKey);
        outbox.setChannel(rule.getNotificationChannel());
        outbox.setMatchedTicketCategoryIds(evaluation.matchedTicketCategoryIds());
        outbox.setMatchedCategoryCount(evaluation.matchedCategoryCount());
        outbox.setMatchedRemaining(evaluation.matchedRemaining());
        outbox.setMinimumPrice(evaluation.minimumPrice());
        outbox.setFreshnessAt(checkedAt);
        outbox.setPublishState(WatchOutboxState.PENDING.name());
        outbox.setAttemptCount(0);
        outbox.setNextAttemptTime(checkedAt);
        outbox.setCreateTime(checkedAt);
        outbox.setEditTime(checkedAt);
        return outbox;
    }
}
