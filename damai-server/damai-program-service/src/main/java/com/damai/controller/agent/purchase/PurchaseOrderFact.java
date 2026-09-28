package com.damai.controller.agent.purchase;

import java.math.BigDecimal;
import java.util.Date;

public record PurchaseOrderFact(
        Long orderNumber,
        Long programId,
        Long userId,
        BigDecimal orderPrice,
        Integer orderStatus,
        Date createdAt) {
}
