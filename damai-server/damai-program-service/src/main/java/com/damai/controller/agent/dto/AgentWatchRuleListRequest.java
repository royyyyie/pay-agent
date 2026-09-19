package com.damai.controller.agent.dto;

import com.damai.controller.agent.watch.WatchRuleState;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.Data;

/** Bounded owner-scoped rule listing. */
@Data
@Schema(title = "AgentWatchRuleListRequest")
public class AgentWatchRuleListRequest {

    private WatchRuleState ruleStatus;

    @Min(1)
    @Max(1000)
    private Integer pageNumber = 1;

    @Min(1)
    @Max(20)
    private Integer pageSize = 10;
}
