package com.damai.controller.agent.watch;

import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.UUID;

/** Database fencing for outbox publishers. */
@Service
public class WatchOutboxService {

    private final WatchNotificationOutboxMapper mapper;

    public WatchOutboxService(WatchNotificationOutboxMapper mapper) {
        this.mapper = mapper;
    }

    public List<WatchNotificationOutbox> claimDue(
            Date now,
            String owner,
            Duration leaseDuration,
            int partitions,
            int limit) {
        int safePartitions = Math.max(1, partitions);
        int safeLimit = Math.max(1, limit);
        Date expiresAt = Date.from(now.toInstant().plus(leaseDuration));
        List<WatchNotificationOutbox> claimed = new ArrayList<>();
        for (int partition = 0; partition < safePartitions && claimed.size() < safeLimit; partition++) {
            List<WatchNotificationOutbox> candidates = mapper.selectDueCandidates(
                    now, partition, safePartitions, safeLimit - claimed.size());
            for (WatchNotificationOutbox candidate : candidates) {
                String token = UUID.randomUUID().toString();
                if (mapper.tryClaim(
                                candidate.getId(),
                                candidate.getProgramId(),
                                now,
                                owner,
                                token,
                                expiresAt)
                        == 1) {
                    candidate.setLeaseOwner(owner);
                    candidate.setLeaseToken(token);
                    candidate.setLeaseExpiresAt(expiresAt);
                    claimed.add(candidate);
                }
            }
        }
        return claimed;
    }

    public boolean markPublished(WatchNotificationOutbox outbox, Date publishedAt) {
        return mapper.markPublished(
                        outbox.getId(),
                        outbox.getProgramId(),
                        outbox.getLeaseOwner(),
                        outbox.getLeaseToken(),
                        publishedAt)
                == 1;
    }

    public boolean markFailed(
            WatchNotificationOutbox outbox,
            Date failedAt,
            Date nextAttemptTime,
            int maximumAttempts) {
        int nextAttempt = outbox.getAttemptCount() + 1;
        String nextState = nextAttempt >= maximumAttempts
                ? WatchOutboxState.DEAD.name()
                : WatchOutboxState.RETRY.name();
        return mapper.markFailed(
                        outbox.getId(),
                        outbox.getProgramId(),
                        outbox.getLeaseOwner(),
                        outbox.getLeaseToken(),
                        nextState,
                        nextAttemptTime,
                        "KAFKA_PUBLISH_FAILED",
                        failedAt)
                == 1;
    }

    public long countDue(Date now) {
        return mapper.countDue(now);
    }
}
