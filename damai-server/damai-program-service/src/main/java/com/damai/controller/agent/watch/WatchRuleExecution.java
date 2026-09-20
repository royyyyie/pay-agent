package com.damai.controller.agent.watch;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/** Append-only audit record for one scheduler attempt. */
@Data
@TableName("d_agent_watch_execution")
public class WatchRuleExecution {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;

    private Long ruleId;

    /** Sharding key, colocated with the watch rule. */
    private Long programId;

    private Long ruleVersion;

    private String leaseToken;

    private Date checkedAt;

    private String outcome;

    private String matchedTicketCategoryIds;

    private Integer matchedCategoryCount;

    private Long matchedRemaining;

    private BigDecimal minimumPrice;

    private String errorCode;

    private Date nextCheckTime;

    private Date createTime;
}
