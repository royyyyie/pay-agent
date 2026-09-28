package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentPurchaseOrderSubmitRequest;
import com.damai.controller.agent.purchase.PurchaseIntentException;
import com.damai.controller.agent.purchase.PurchaseOrderSubmissionCoordinator;
import com.damai.controller.agent.purchase.PurchaseSecurityMetrics;
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

/** ORDER_WRITE endpoint, absent unless both purchase and order submission switches are enabled. */
@Slf4j
@RestController
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = {"enabled", "order-submission-enabled"},
        havingValue = "true")
@RequestMapping("/internal/agent/v1/tools/purchase-intents")
public class AgentPurchaseOrderToolController {

    private static final String TOOL_CALL_ID = "X-Agent-Tool-Call-Id";
    private static final String SESSION_KEY = "X-Agent-Session-Key";
    private static final String TENANT_ID = "X-Agent-Tenant-Id";
    private static final String USER_ID = "X-Agent-User-Id";

    private final PurchaseOrderSubmissionCoordinator coordinator;
    private final PurchaseSecurityMetrics metrics;

    public AgentPurchaseOrderToolController(
            PurchaseOrderSubmissionCoordinator coordinator,
            PurchaseSecurityMetrics metrics) {
        this.coordinator = coordinator;
        this.metrics = metrics;
    }

    @PostMapping("/submit")
    public AgentToolResponse<AgentPurchaseIntentVo> submit(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(SESSION_KEY) String sessionKey,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @Valid @RequestBody AgentPurchaseOrderSubmitRequest request) {
        return AgentToolResponse.ok(
                requestId,
                coordinator.submit(tenantId, userId, sessionKey, requestId, request));
    }

    @ExceptionHandler(PurchaseIntentException.class)
    public AgentToolResponse<Void> handleBusinessError(
            PurchaseIntentException exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        metrics.submissionRejected("business");
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
        metrics.submissionRejected("validation");
        return AgentToolResponse.error(requestId, 400, "订单提交参数不合法", false);
    }

    @ExceptionHandler(Throwable.class)
    public AgentToolResponse<Void> handleUnexpectedError(
            Throwable exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        metrics.submissionRejected("unexpected");
        log.error("Agent order submission failed, requestId={}", requestId, exception);
        return AgentToolResponse.error(requestId, -100, "订单提交服务暂时不可用", true);
    }
}
