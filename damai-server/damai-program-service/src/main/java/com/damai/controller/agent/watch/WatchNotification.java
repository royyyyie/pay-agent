package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/** Durable in-app notification or suppression receipt. */
@Data
@TableName("d_agent_watch_notification")
public class WatchNotification {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;
    private String eventId;
    private Long ruleId;
    private Long programId;
    private Long ruleVersion;
    private String tenantId;
    private String userId;
    private String channel;
    private String deliveryState;
    private String title;
    private String content;
    private String matchedTicketCategoryIds;
    private Integer matchedCategoryCount;
    private Long matchedRemaining;
    private BigDecimal minimumPrice;
    private Date freshnessAt;
    private Date createTime;
}
