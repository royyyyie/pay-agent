package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.util.Date;

@Data
@TableName("d_agent_purchase_intent")
public class PurchaseIntent {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;
    private String tenantId;
    private String userId;
    private String sessionKey;
    private String idempotencyKey;
    private Long programId;
    private Long ticketCategoryId;
    private Integer quantity;
    private Long unitAmountFen;
    private Long totalAmountFen;
    private String currency;
    private String quoteHash;
    private Date quoteExpiresAt;
    private String intentState;
    private Long version;
    private Date confirmedAt;
    private Date submittedAt;
    private Date createTime;
    private Date editTime;
}
