package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/** Persistent control-plane state for one ticket availability watch. */
@Data
@TableName("d_agent_watch_rule")
public class WatchRule {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;

    private String tenantId;

    private String userId;

    private String idempotencyKey;

    /** Sharding key. It is immutable after creation. */
    private Long programId;

    private String name;

    /** Sorted comma-separated positive IDs; null means all ticket categories. */
    private String ticketCategoryIds;

    private BigDecimal maxPrice;

    private Long minRemaining;

    private Integer checkIntervalSeconds;

    private String notificationChannel;

    private String ruleState;

    private Date nextCheckTime;

    private Date lastCheckedTime;

    private Date lastTriggeredTime;

    private Long version;

    @TableField(fill = FieldFill.INSERT)
    private Date createTime;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private Date editTime;
}
