package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;

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
}
