package com.damai.controller.agent.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

/** Safe projection: no document, phone, address, or full legal name. */
@Data
@AllArgsConstructor
public class AgentPurchaseAttendeeRefVo {

    private Long ticketUserId;
    private String displayLabel;
}
