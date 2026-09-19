package com.damai.controller.agent.vo;

import com.damai.controller.agent.dto.AgentRecommendationPreference;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * 有界扫描后的推荐结果。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Schema(title = "AgentProgramRecommendationPage", description = "实时推荐结果")
public class AgentProgramRecommendationPageVo {

    private Integer scannedCount;

    private Integer eligibleCount;

    private AgentRecommendationPreference preference;

    private List<AgentProgramRecommendationVo> list;
}
