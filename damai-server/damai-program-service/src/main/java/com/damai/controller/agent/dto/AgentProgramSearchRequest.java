package com.damai.controller.agent.dto;

import com.damai.dto.ProgramSearchDto;
import com.fasterxml.jackson.annotation.JsonIgnore;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.math.BigDecimal;
import java.util.Date;

/**
 * Agent 节目搜索入参。该对象隔离 Agent 契约与面向前端的业务 DTO。
 */
@Data
@Schema(title = "AgentProgramSearchRequest", description = "Agent 节目搜索参数")
public class AgentProgramSearchRequest {

    @Size(max = 100)
    @Schema(description = "节目名、艺人或搜索关键词")
    private String keyword;

    @Schema(description = "城市或区域 id")
    private Long areaId;

    @Schema(description = "父节目分类 id")
    private Long parentProgramCategoryId;

    @Schema(description = "节目分类 id")
    private Long programCategoryId;

    @DecimalMin(value = "0.00")
    @Digits(integer = 9, fraction = 2)
    @Schema(description = "推荐预算硬上限；最低票价超过该值的候选会被移除")
    private BigDecimal maxPrice;

    @Min(0)
    @Max(5)
    @Schema(description = "时间范围：0 全部、1 今天、2 明天、3 一周内、4 一月内、5 自定义")
    private Integer timeType = 0;

    @Schema(description = "自定义开始时间，timeType=5 时使用")
    private Date startDateTime;

    @Schema(description = "自定义结束时间，timeType=5 时使用")
    private Date endDateTime;

    @Min(1)
    @Max(4)
    @Schema(description = "排序方式：1 相关度、2 推荐、3 最近开场、4 最新上架")
    private Integer sortType = 1;

    @Min(1)
    @Schema(description = "页码")
    private Integer pageNumber = 1;

    @Min(1)
    @Max(20)
    @Schema(description = "每页条数，Agent 侧最多查询 20 条")
    private Integer pageSize = 10;

    @JsonIgnore
    @AssertTrue(message = "自定义时间范围必须同时提供有效的开始和结束时间")
    public boolean isCustomDateRangeValid() {
        if (!Integer.valueOf(5).equals(timeType)) {
            return true;
        }
        return startDateTime != null
                && endDateTime != null
                && !startDateTime.after(endDateTime);
    }

    public ProgramSearchDto toProgramSearchDto() {
        ProgramSearchDto dto = new ProgramSearchDto();
        dto.setContent(keyword);
        dto.setAreaId(areaId);
        dto.setParentProgramCategoryId(parentProgramCategoryId);
        dto.setProgramCategoryId(programCategoryId);
        dto.setTimeType(timeType);
        dto.setStartDateTime(startDateTime);
        dto.setEndDateTime(endDateTime);
        dto.setType(sortType);
        dto.setPageNumber(pageNumber);
        dto.setPageSize(pageSize);
        return dto;
    }
}
