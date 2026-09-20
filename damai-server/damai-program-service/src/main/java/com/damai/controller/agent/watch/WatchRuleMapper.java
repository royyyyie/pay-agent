package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.Date;
import java.util.List;

/** MyBatis mapper kept beside the Agent control plane so contract CI compiles it. */
@Mapper
public interface WatchRuleMapper extends BaseMapper<WatchRule> {

    @Insert("""
            INSERT INTO d_agent_watch_rule (
                id, tenant_id, user_id, idempotency_key, program_id, name,
                ticket_category_ids, max_price, min_remaining, check_interval_seconds,
                notification_channel, rule_state, next_check_time, version,
                create_time, edit_time
            ) VALUES (
                #{id}, #{tenantId}, #{userId}, #{idempotencyKey}, #{programId}, #{name},
                #{ticketCategoryIds}, #{maxPrice}, #{minRemaining}, #{checkIntervalSeconds},
                #{notificationChannel}, #{ruleState}, #{nextCheckTime}, #{version},
                #{createTime}, #{editTime}
            ) ON DUPLICATE KEY UPDATE id = id
            """)
    int insertIdempotent(WatchRule rule);

    /**
     * The modulo predicate gives every scan a bounded shard partition. The following update is the
     * actual concurrency boundary, so duplicate candidates returned to replicas are harmless.
     */
    @Select("""
            SELECT *
            FROM d_agent_watch_rule
            WHERE rule_state = 'ACTIVE'
              AND next_check_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
              AND MOD(program_id, #{partitionCount}) = #{partition}
            ORDER BY next_check_time, id
            LIMIT #{limit}
            """)
    List<WatchRule> selectDueCandidates(
            @Param("now") Date now,
            @Param("partition") int partition,
            @Param("partitionCount") int partitionCount,
            @Param("limit") int limit);

    @Update("""
            UPDATE d_agent_watch_rule
            SET lease_owner = #{leaseOwner},
                lease_token = #{leaseToken},
                lease_expires_at = #{leaseExpiresAt},
                claimed_version = version
            WHERE id = #{id}
              AND program_id = #{programId}
              AND version = #{version}
              AND rule_state = 'ACTIVE'
              AND next_check_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
            """)
    int tryClaim(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("version") Long version,
            @Param("now") Date now,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("leaseExpiresAt") Date leaseExpiresAt);

    @Update("""
            UPDATE d_agent_watch_rule
            SET last_checked_time = #{checkedAt},
                next_check_time = #{nextCheckTime},
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                claimed_version = NULL
            WHERE id = #{id}
              AND program_id = #{programId}
              AND version = #{ruleVersion}
              AND claimed_version = #{ruleVersion}
              AND rule_state = 'ACTIVE'
              AND lease_owner = #{leaseOwner}
              AND lease_token = #{leaseToken}
              AND lease_expires_at >= #{checkedAt}
            """)
    int completeClaim(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("ruleVersion") Long ruleVersion,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("checkedAt") Date checkedAt,
            @Param("nextCheckTime") Date nextCheckTime);

    @Select("""
            SELECT COUNT(*)
            FROM d_agent_watch_rule
            WHERE rule_state = 'ACTIVE'
              AND next_check_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
            """)
    long countDue(@Param("now") Date now);
}
