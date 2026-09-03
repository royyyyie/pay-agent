package com.damai.controller.agent.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * Agent 根据节目 id 查询数据时使用的统一入参。
 */
@Data
@Schema(title = "AgentProgramRequest", description = "Agent 节目查询参数")
public class AgentProgramRequest {

    @NotNull
    @Schema(description = "节目 id", requiredMode = Schema.RequiredMode.REQUIRED)
    private Long programId;
}
