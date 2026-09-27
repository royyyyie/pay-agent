package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/** Transactional outbox row colocated with its watch rule by program_id. */
@Data
@TableName("d_agent_watch_outbox")
public class WatchNotificationOutbox {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;
    private String eventId;
    private Long ruleId;
    private Long programId;
    private Long ruleVersion;
    private Long executionId;
    private String conditionFingerprint;
    private Date dedupeWindowStart;
    private String dedupeKey;
    private String channel;
    private String matchedTicketCategoryIds;
    private Integer matchedCategoryCount;
    private Long matchedRemaining;
    private BigDecimal minimumPrice;
    private Date freshnessAt;
    private String publishState;
    private Integer attemptCount;
    private Date nextAttemptTime;
    private String leaseOwner;
    private String leaseToken;
    private Date leaseExpiresAt;
    private String lastErrorCode;
    private Date publishedAt;
    private Date createTime;
    private Date editTime;
}
