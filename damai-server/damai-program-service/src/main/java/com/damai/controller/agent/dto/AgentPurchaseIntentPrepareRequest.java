package com.damai.controller.agent.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class AgentPurchaseIntentPrepareRequest {

    @NotNull
    @Min(1)
    private Long programId;

    @NotNull
    @Min(1)
    private Long ticketCategoryId;

    @NotNull
    @Min(1)
    @Max(6)
    private Integer quantity = 1;
}
