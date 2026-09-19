package com.damai.controller.agent.dto;

import com.fasterxml.jackson.annotation.JsonIgnore;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.AssertTrue;
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

/** Optimistic patch for a watch rule. Program ID is retained as the sharding key. */
@Data
@Schema(title = "AgentWatchRuleUpdateRequest")
public class AgentWatchRuleUpdateRequest {

    @NotNull
    @Positive
    private Long ruleId;

    @NotNull
    @Positive
    private Long programId;

    @NotNull
    @Positive
    private Long expectedVersion;

    @Size(min = 1, max = 100)
    private String name;

    @Size(max = 20)
    private List<@NotNull @Positive Long> ticketCategoryIds;

    @DecimalMin("0.00")
    @DecimalMax("999999999.99")
    private BigDecimal maxPrice;

    private Boolean clearMaxPrice = false;

    @Min(1)
    @Max(1000000)
    private Long minRemaining;

    @Min(30)
    @Max(86400)
    private Integer checkIntervalSeconds;

    @JsonIgnore
    @AssertTrue(message = "必须提供至少一个修改字段，且价格设置不能冲突")
    public boolean isPatchValid() {
        boolean clearPrice = Boolean.TRUE.equals(clearMaxPrice);
        boolean hasPatch = name != null
                || ticketCategoryIds != null
                || maxPrice != null
                || clearPrice
                || minRemaining != null
                || checkIntervalSeconds != null;
        return hasPatch && !(clearPrice && maxPrice != null);
    }
}
