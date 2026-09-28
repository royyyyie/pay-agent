package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentPurchaseAttendeeListRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentCancelRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentGetRequest;
import com.damai.controller.agent.dto.AgentPurchaseIntentPrepareRequest;
import com.damai.controller.agent.purchase.PurchaseAttendeeService;
import com.damai.controller.agent.purchase.PurchaseIntentException;
import com.damai.controller.agent.purchase.PurchaseIntentService;
import com.damai.controller.agent.vo.AgentPurchaseAttendeeRefVo;
import com.damai.controller.agent.vo.AgentPurchaseIntentVo;
import com.damai.controller.agent.vo.AgentToolResponse;
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

import java.util.List;

/** Model-visible purchase-intent tools. No confirmation or order endpoint is exposed here. */
@Slf4j
@RestController
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = "enabled",
        havingValue = "true")
@RequestMapping("/internal/agent/v1/tools/purchase-intents")
public class AgentPurchaseIntentToolController {

    private static final String TOOL_CALL_ID = "X-Agent-Tool-Call-Id";
    private static final String TURN_ID = "X-Agent-Turn-Id";
    private static final String SESSION_KEY = "X-Agent-Session-Key";
    private static final String TENANT_ID = "X-Agent-Tenant-Id";
    private static final String USER_ID = "X-Agent-User-Id";

    private final PurchaseIntentService service;
    private final PurchaseAttendeeService attendeeService;

    public AgentPurchaseIntentToolController(
            PurchaseIntentService service, PurchaseAttendeeService attendeeService) {
        this.service = service;
        this.attendeeService = attendeeService;
    }

    @PostMapping("/attendees")
    public AgentToolResponse<List<AgentPurchaseAttendeeRefVo>> attendees(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentPurchaseAttendeeListRequest request) {
        return AgentToolResponse.ok(requestId, attendeeService.list(userId));
    }

    @PostMapping("/prepare")
    public AgentToolResponse<AgentPurchaseIntentVo> prepare(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TURN_ID) String turnId,
            @RequestHeader(SESSION_KEY) String sessionKey,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentPurchaseIntentPrepareRequest request) {
        return AgentToolResponse.ok(
                requestId,
                service.prepare(tenantId, userId, sessionKey, turnId, request));
    }

    @PostMapping("/get")
    public AgentToolResponse<AgentPurchaseIntentVo> get(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentPurchaseIntentGetRequest request) {
        return AgentToolResponse.ok(requestId, service.get(tenantId, userId, request));
    }

    @PostMapping("/cancel")
    public AgentToolResponse<AgentPurchaseIntentVo> cancel(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentPurchaseIntentCancelRequest request) {
        return AgentToolResponse.ok(requestId, service.cancel(tenantId, userId, request));
    }

    @ExceptionHandler(PurchaseIntentException.class)
    public AgentToolResponse<Void> handleBusinessError(
            PurchaseIntentException exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        return AgentToolResponse.error(
                requestId,
                exception.getCode(),
                exception.getMessage(),
                exception.getCode() >= 500);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, BindException.class})
    public AgentToolResponse<Void> handleValidationError(
            Exception exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        return AgentToolResponse.error(requestId, 400, "购买意向参数不合法", false);
    }

    @ExceptionHandler(Throwable.class)
    public AgentToolResponse<Void> handleUnexpectedError(
            Throwable exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        log.error("Agent purchase-intent operation failed, requestId={}", requestId, exception);
        return AgentToolResponse.error(requestId, -100, "购买意向服务暂时不可用", true);
    }
}
