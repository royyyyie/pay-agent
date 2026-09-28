package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.util.Date;

/** Durable command and reconciliation fact colocated with its purchase intent. */
@Data
@TableName("d_agent_order_submission")
public class PurchaseOrderSubmission {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;
    private Long intentId;
    private Long programId;
    private Long intentVersion;
    private String tenantId;
    private String userId;
    private String sessionKey;
    private Long ticketCategoryId;
    private Integer quantity;
    private String ticketUserRefs;
    private Long unitAmountFen;
    private Long totalAmountFen;
    private String currency;
    private Long orderNumber;
    private String submissionState;
    private Integer attemptCount;
    private Date nextAttemptTime;
    private String leaseOwner;
    private String leaseToken;
    private Date leaseExpiresAt;
    private String lastErrorCode;
    private Date submittedAt;
    private Date createTime;
    private Date editTime;

    @TableField(exist = false)
    private boolean reclaimedUnknown;
}
