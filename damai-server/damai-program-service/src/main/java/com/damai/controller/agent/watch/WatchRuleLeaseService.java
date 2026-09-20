package com.damai.controller.agent.watch;

import com.baidu.fsg.uid.UidGenerator;
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
    private final UidGenerator uidGenerator;

    public WatchRuleLeaseService(
            WatchRuleMapper watchRuleMapper,
            WatchRuleExecutionMapper executionMapper,
            UidGenerator uidGenerator) {
        this.watchRuleMapper = watchRuleMapper;
        this.executionMapper = executionMapper;
        this.uidGenerator = uidGenerator;
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
        int updated = watchRuleMapper.completeClaim(
                claimedRule.getId(),
                claimedRule.getProgramId(),
                claimedRule.getClaimedVersion(),
                claimedRule.getLeaseOwner(),
                claimedRule.getLeaseToken(),
                checkedAt,
                nextCheckTime);
        if (updated != 1) {
            return false;
        }
        if (executionMapper.insertIdempotent(toExecution(
                        claimedRule, evaluation, checkedAt, nextCheckTime)) != 1) {
            throw new IllegalStateException("watch execution was not persisted");
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
}
