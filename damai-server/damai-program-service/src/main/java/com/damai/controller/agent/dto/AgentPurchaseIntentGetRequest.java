package com.damai.controller.agent.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class AgentPurchaseIntentGetRequest {

    @NotNull
    @Min(1)
    private Long intentId;

    @NotNull
    @Min(1)
    private Long programId;
}
