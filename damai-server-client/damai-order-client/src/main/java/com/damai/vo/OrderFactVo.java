package com.damai.vo;

import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/** PII-free immutable order facts for service-to-service reconciliation. */
@Data
public class OrderFactVo {

    private Long orderNumber;
    private Long programId;
    private Long userId;
    private BigDecimal orderPrice;
    private Integer orderStatus;
    private Date createOrderTime;
}
