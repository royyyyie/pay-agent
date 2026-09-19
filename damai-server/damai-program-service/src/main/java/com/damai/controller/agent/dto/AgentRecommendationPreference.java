package com.damai.controller.agent.dto;

/**
 * 只在硬约束和实时库存过滤后生效的推荐排序偏好。
 */
public enum AgentRecommendationPreference {
    RELEVANCE,
    LOWEST_PRICE,
    EARLIEST_SHOW,
    MOST_AVAILABLE
}
