package com.damai.controller.agent.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;

/**
 * Java Tool Gateway 的稳定返回结构。
 *
 * @param <T> 工具业务数据类型
 */
@Data
@Schema(title = "AgentToolResponse", description = "Agent 工具统一响应")
public class AgentToolResponse<T> {

    @Schema(description = "本次工具调用 id")
    private String requestId;

    @Schema(description = "工具是否成功执行")
    private boolean success;

    @Schema(description = "业务状态码，0 表示成功")
    private Integer code;

    @Schema(description = "适合提供给 Agent 的简短信息")
    private String message;

    @Schema(description = "工具返回的结构化业务数据")
    private T data;

    @Schema(description = "失败后是否适合自动重试")
    private boolean retryable;

    @Schema(description = "数据生成时间，UTC ISO-8601")
    private String freshnessAt;

    public static <T> AgentToolResponse<T> ok(String requestId, T data) {
        AgentToolResponse<T> response = base(requestId);
        response.success = true;
        response.code = 0;
        response.message = "success";
        response.data = data;
        return response;
    }

    public static <T> AgentToolResponse<T> error(
            String requestId, Integer code, String message, boolean retryable) {
        AgentToolResponse<T> response = base(requestId);
        response.success = false;
        response.code = code;
        response.message = message;
        response.retryable = retryable;
        return response;
    }

    private static <T> AgentToolResponse<T> base(String requestId) {
        AgentToolResponse<T> response = new AgentToolResponse<>();
        response.requestId = requestId;
        response.freshnessAt = OffsetDateTime.now(ZoneOffset.UTC).toString();
        return response;
    }
}
