package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface WatchNotificationMapper extends BaseMapper<WatchNotification> {

    @Insert("""
            INSERT INTO d_agent_watch_notification (
                id, event_id, rule_id, program_id, rule_version, tenant_id, user_id,
                channel, delivery_state, title, content, matched_ticket_category_ids,
                matched_category_count, matched_remaining, minimum_price, freshness_at, create_time
            ) VALUES (
                #{id}, #{eventId}, #{ruleId}, #{programId}, #{ruleVersion}, #{tenantId}, #{userId},
                #{channel}, #{deliveryState}, #{title}, #{content}, #{matchedTicketCategoryIds},
                #{matchedCategoryCount}, #{matchedRemaining}, #{minimumPrice}, #{freshnessAt}, #{createTime}
            ) ON DUPLICATE KEY UPDATE id = id
            """)
    int insertIdempotent(WatchNotification notification);
}
