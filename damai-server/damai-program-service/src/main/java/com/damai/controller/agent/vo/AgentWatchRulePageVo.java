package com.damai.controller.agent.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.List;

/** Bounded watch rule page returned to the Agent. */
@Data
@AllArgsConstructor
public class AgentWatchRulePageVo {

    private Integer pageNumber;

    private Integer pageSize;

    private Long totalSize;

    private List<AgentWatchRuleVo> list;
}
