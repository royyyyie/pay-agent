package com.damai.controller.agent.dto;

import com.damai.controller.agent.watch.WatchRuleState;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import lombok.Data;

/** Pause or resume request guarded by owner, shard key, and optimistic version. */
@Data
@Schema(title = "AgentWatchRuleStatusRequest")
public class AgentWatchRuleStatusRequest {

    @NotNull
    @Positive
    private Long ruleId;

    @NotNull
    @Positive
    private Long programId;

    @NotNull
    @Positive
    private Long expectedVersion;

    @NotNull
    private WatchRuleState targetStatus;
}
