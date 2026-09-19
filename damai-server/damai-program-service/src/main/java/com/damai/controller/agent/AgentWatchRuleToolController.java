package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentWatchRuleCreateRequest;
import com.damai.controller.agent.dto.AgentWatchRuleListRequest;
import com.damai.controller.agent.dto.AgentWatchRuleStatusRequest;
import com.damai.controller.agent.dto.AgentWatchRuleUpdateRequest;
import com.damai.controller.agent.vo.AgentToolResponse;
import com.damai.controller.agent.vo.AgentWatchRulePageVo;
import com.damai.controller.agent.vo.AgentWatchRuleVo;
import com.damai.controller.agent.watch.WatchRuleException;
import com.damai.controller.agent.watch.WatchRuleService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.validation.BindException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Trusted Agent control plane for durable watch rules. */
@Slf4j
@RestController
@ConditionalOnProperty(
        prefix = "agent.watch-rules",
        name = "enabled",
        havingValue = "true")
@RequestMapping("/internal/agent/v1/tools/watch-rules")
@Tag(name = "agent-watch-rule-tools", description = "Python Agent 使用的票务监控规则工具")
public class AgentWatchRuleToolController {

    private static final String TOOL_CALL_ID = "X-Agent-Tool-Call-Id";
    private static final String TURN_ID = "X-Agent-Turn-Id";
    private static final String TENANT_ID = "X-Agent-Tenant-Id";
    private static final String USER_ID = "X-Agent-User-Id";

    private final WatchRuleService watchRuleService;

    public AgentWatchRuleToolController(WatchRuleService watchRuleService) {
        this.watchRuleService = watchRuleService;
    }

    @Operation(summary = "创建票务监控规则")
    @PostMapping("/create")
    public AgentToolResponse<AgentWatchRuleVo> create(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TURN_ID) String turnId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentWatchRuleCreateRequest request) {
        return AgentToolResponse.ok(
                requestId,
                watchRuleService.create(tenantId, userId, turnId, request));
    }

    @Operation(summary = "修改票务监控规则")
    @PostMapping("/update")
    public AgentToolResponse<AgentWatchRuleVo> update(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentWatchRuleUpdateRequest request) {
        return AgentToolResponse.ok(
                requestId,
                watchRuleService.update(tenantId, userId, request));
    }

    @Operation(summary = "暂停或恢复票务监控规则")
    @PostMapping("/status")
    public AgentToolResponse<AgentWatchRuleVo> setStatus(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentWatchRuleStatusRequest request) {
        return AgentToolResponse.ok(
                requestId,
                watchRuleService.setStatus(tenantId, userId, request));
    }

    @Operation(summary = "查询本人票务监控规则")
    @PostMapping("/list")
    public AgentToolResponse<AgentWatchRulePageVo> list(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentWatchRuleListRequest request) {
        return AgentToolResponse.ok(
                requestId,
                watchRuleService.list(tenantId, userId, request));
    }

    @ExceptionHandler(WatchRuleException.class)
    public AgentToolResponse<Void> handleBusinessError(
            WatchRuleException exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        return AgentToolResponse.error(requestId, exception.getCode(), exception.getMessage(), false);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, BindException.class})
    public AgentToolResponse<Void> handleValidationError(
            Exception exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        return AgentToolResponse.error(requestId, 400, "Agent 监控规则参数不合法", false);
    }

    @ExceptionHandler(Throwable.class)
    public AgentToolResponse<Void> handleUnexpectedError(
            Throwable exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        log.error("Agent watch-rule operation failed, requestId={}", requestId, exception);
        return AgentToolResponse.error(requestId, -100, "监控规则服务暂时不可用", true);
    }
}
