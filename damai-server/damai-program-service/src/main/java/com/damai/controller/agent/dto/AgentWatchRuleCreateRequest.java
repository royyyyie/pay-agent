package com.damai.controller.agent.dto;

import com.damai.controller.agent.watch.WatchNotificationChannel;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.math.BigDecimal;
import java.util.List;

/** Create request for a durable ticket watch. Trusted ownership comes from headers. */
@Data
@Schema(title = "AgentWatchRuleCreateRequest")
public class AgentWatchRuleCreateRequest {

    @NotNull
    @Positive
    private Long programId;

    @Size(min = 1, max = 100)
    private String name;

    @Size(max = 20)
    private List<@NotNull @Positive Long> ticketCategoryIds;

    @DecimalMin("0.00")
    @DecimalMax("999999999.99")
    private BigDecimal maxPrice;

    @NotNull
    @Min(1)
    @Max(1000000)
    private Long minRemaining = 1L;

    @NotNull
    @Min(30)
    @Max(86400)
    private Integer checkIntervalSeconds = 300;

    @NotNull
    @Valid
    private WatchNotificationChannel notificationChannel = WatchNotificationChannel.IN_APP;
}
