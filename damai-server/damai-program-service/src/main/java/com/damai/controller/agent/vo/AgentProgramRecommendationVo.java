package com.damai.controller.agent.vo;

import com.damai.vo.ProgramListVo;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.math.BigDecimal;
import java.util.List;

/**
 * 已完成实时票档核验的推荐候选。
 */
@Data
@EqualsAndHashCode(callSuper = true)
@Schema(title = "AgentProgramRecommendation", description = "已核验实时余票的推荐候选")
public class AgentProgramRecommendationVo extends ProgramListVo {

    private Integer rank;

    private Integer availableTicketCategoryCount;

    private BigDecimal lowestAvailablePrice;

    private Long totalRemaining;

    private List<String> reasonCodes;
}
