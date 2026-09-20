package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface WatchRuleExecutionMapper extends BaseMapper<WatchRuleExecution> {

    @Insert("""
            INSERT IGNORE INTO d_agent_watch_execution (
                id, rule_id, program_id, rule_version, lease_token, checked_at, outcome,
                matched_ticket_category_ids, matched_category_count, matched_remaining,
                minimum_price, error_code, next_check_time, create_time
            ) VALUES (
                #{id}, #{ruleId}, #{programId}, #{ruleVersion}, #{leaseToken}, #{checkedAt}, #{outcome},
                #{matchedTicketCategoryIds}, #{matchedCategoryCount}, #{matchedRemaining},
                #{minimumPrice}, #{errorCode}, #{nextCheckTime}, #{createTime}
            )
            """)
    int insertIdempotent(WatchRuleExecution execution);
}
