package com.damai.controller.agent.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.util.List;

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

    @NotNull
    @Size(min = 1, max = 6)
    private List<@NotNull @Min(1) Long> ticketUserIds;
}
