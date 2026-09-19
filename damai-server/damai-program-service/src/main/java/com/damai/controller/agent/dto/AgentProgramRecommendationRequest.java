package com.damai.controller.agent.dto;

import com.damai.dto.ProgramSearchDto;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * Agent 推荐请求。服务端限制扫描窗口，避免推荐触发无界库存查询。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@Schema(title = "AgentProgramRecommendationRequest", description = "Agent 实时推荐参数")
public class AgentProgramRecommendationRequest extends AgentProgramSearchRequest {

    private static final int MAX_SCAN_SIZE = 10;

    @NotNull
    @Schema(description = "硬约束和实时余票过滤后的软排序偏好")
    private AgentRecommendationPreference preference = AgentRecommendationPreference.RELEVANCE;

    @NotNull
    @Min(1)
    @Max(5)
    @Schema(description = "最多返回候选数量")
    private Integer candidateLimit = 3;

    public ProgramSearchDto toRecommendationSearchDto() {
        ProgramSearchDto dto = super.toProgramSearchDto();
        int resolvedLimit = candidateLimit == null ? 3 : candidateLimit;
        dto.setPageNumber(1);
        dto.setPageSize(Math.min(MAX_SCAN_SIZE, Math.max(resolvedLimit * 2, resolvedLimit)));
        return dto;
    }
}
