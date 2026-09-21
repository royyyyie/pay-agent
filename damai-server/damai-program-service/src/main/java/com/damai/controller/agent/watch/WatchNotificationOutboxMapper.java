package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.Date;
import java.util.List;

@Mapper
public interface WatchNotificationOutboxMapper extends BaseMapper<WatchNotificationOutbox> {

    @Insert("""
            INSERT INTO d_agent_watch_outbox (
                id, event_id, rule_id, program_id, rule_version, execution_id,
                condition_fingerprint, dedupe_window_start, dedupe_key, channel,
                matched_ticket_category_ids, matched_category_count, matched_remaining,
                minimum_price, freshness_at, publish_state, attempt_count,
                next_attempt_time, create_time, edit_time
            ) VALUES (
                #{id}, #{eventId}, #{ruleId}, #{programId}, #{ruleVersion}, #{executionId},
                #{conditionFingerprint}, #{dedupeWindowStart}, #{dedupeKey}, #{channel},
                #{matchedTicketCategoryIds}, #{matchedCategoryCount}, #{matchedRemaining},
                #{minimumPrice}, #{freshnessAt}, #{publishState}, #{attemptCount},
                #{nextAttemptTime}, #{createTime}, #{editTime}
            ) ON DUPLICATE KEY UPDATE id = id
            """)
    int insertIdempotent(WatchNotificationOutbox outbox);

    @Select("""
            SELECT *
            FROM d_agent_watch_outbox
            WHERE publish_state IN ('PENDING', 'RETRY')
              AND next_attempt_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
              AND MOD(program_id, #{partitionCount}) = #{partition}
            ORDER BY next_attempt_time, id
            LIMIT #{limit}
            """)
    List<WatchNotificationOutbox> selectDueCandidates(
            @Param("now") Date now,
            @Param("partition") int partition,
            @Param("partitionCount") int partitionCount,
            @Param("limit") int limit);

    @Update("""
            UPDATE d_agent_watch_outbox
            SET lease_owner = #{leaseOwner}, lease_token = #{leaseToken},
                lease_expires_at = #{leaseExpiresAt}, edit_time = #{now}
            WHERE id = #{id} AND program_id = #{programId}
              AND publish_state IN ('PENDING', 'RETRY')
              AND next_attempt_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
            """)
    int tryClaim(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("now") Date now,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("leaseExpiresAt") Date leaseExpiresAt);

    @Update("""
            UPDATE d_agent_watch_outbox
            SET publish_state = 'PUBLISHED', attempt_count = attempt_count + 1,
                published_at = #{publishedAt}, last_error_code = NULL,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                edit_time = #{publishedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND publish_state IN ('PENDING', 'RETRY')
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markPublished(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("publishedAt") Date publishedAt);

    @Update("""
            UPDATE d_agent_watch_outbox
            SET publish_state = #{nextState}, attempt_count = attempt_count + 1,
                next_attempt_time = #{nextAttemptTime}, last_error_code = #{errorCode},
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                edit_time = #{failedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND publish_state IN ('PENDING', 'RETRY')
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markFailed(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("nextState") String nextState,
            @Param("nextAttemptTime") Date nextAttemptTime,
            @Param("errorCode") String errorCode,
            @Param("failedAt") Date failedAt);

    @Select("""
            SELECT COUNT(*) FROM d_agent_watch_outbox
            WHERE publish_state IN ('PENDING', 'RETRY') AND next_attempt_time <= #{now}
            """)
    long countDue(@Param("now") Date now);
}
