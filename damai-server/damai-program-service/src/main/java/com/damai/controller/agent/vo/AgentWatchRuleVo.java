package com.damai.controller.agent.vo;

import lombok.Data;

import java.math.BigDecimal;
import java.util.List;

/** Owner-safe watch rule projection. Tenant, user, and idempotency data never reach the model. */
@Data
public class AgentWatchRuleVo {

    private Long ruleId;

    private Long programId;

    private String name;

    private List<Long> ticketCategoryIds;

    private BigDecimal maxPrice;

    private Long minRemaining;

    private Integer checkIntervalSeconds;

    private String notificationChannel;

    private String ruleStatus;

    private Long version;

    private String nextCheckAt;

    private String lastCheckedAt;

    private String lastTriggeredAt;

    private String createdAt;

    private String updatedAt;
}
