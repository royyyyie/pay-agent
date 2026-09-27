package com.damai.controller.agent.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class AgentPurchaseConfirmationRequest {

    @NotNull
    @Min(1)
    private Long intentId;

    @NotNull
    @Min(1)
    private Long programId;

    @NotNull
    @Min(1)
    private Long expectedVersion;

    @NotBlank
    @Pattern(regexp = "^[0-9a-f]{64}$")
    private String quoteHash;

    @NotBlank
    @Size(min = 16, max = 128)
    @Pattern(regexp = "^[A-Za-z0-9_-]+$")
    private String confirmationNonce;

    @NotBlank
    private String confirmedAt;
}
