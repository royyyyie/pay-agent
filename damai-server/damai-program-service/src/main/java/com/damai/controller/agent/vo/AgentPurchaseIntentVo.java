package com.damai.controller.agent.vo;

import lombok.Data;

import java.util.List;

@Data
public class AgentPurchaseIntentVo {

    private Long intentId;
    private Long programId;
    private Long ticketCategoryId;
    private Integer quantity;
    private List<Long> ticketUserIds;
    private Long unitAmountFen;
    private Long totalAmountFen;
    private String currency;
    private String quoteHash;
    private String quoteExpiresAt;
    private String intentStatus;
    private Long version;
    private String createdAt;
    private String updatedAt;
    private Long orderNumber;
    private String submittedAt;
}
