package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentPurchaseConfirmationRequest;
import com.damai.controller.agent.purchase.ConfirmationProof;
import com.damai.controller.agent.purchase.ConfirmationProofVerifier;
import com.damai.controller.agent.purchase.PurchaseIntentException;
import com.damai.controller.agent.purchase.PurchaseIntentService;
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

/** BFF-only explicit confirmation endpoint; deliberately absent from the Agent Tool contract. */
@Slf4j
@RestController
@ConditionalOnProperty(
        prefix = "agent.purchase-intents",
        name = "enabled",
        havingValue = "true")
@RequestMapping("/internal/agent/v1/confirmations/purchase-intents")
public class AgentPurchaseConfirmationController {

    private static final String TOOL_CALL_ID = "X-Agent-Tool-Call-Id";
    private static final String SESSION_KEY = "X-Agent-Session-Key";
    private static final String TENANT_ID = "X-Agent-Tenant-Id";
    private static final String USER_ID = "X-Agent-User-Id";
    private static final String CONFIRMATION_SIGNATURE = "X-Agent-Confirmation-Signature";

    private final ConfirmationProofVerifier proofVerifier;
    private final PurchaseIntentService service;
    private final PurchaseSecurityMetrics metrics;

    public AgentPurchaseConfirmationController(
            ConfirmationProofVerifier proofVerifier,
            PurchaseIntentService service,
            PurchaseSecurityMetrics metrics) {
        this.proofVerifier = proofVerifier;
        this.service = service;
        this.metrics = metrics;
    }

    @PostMapping("/issue")
    public AgentToolResponse<AgentPurchaseIntentVo> issue(
            @RequestHeader(TOOL_CALL_ID) String requestId,
            @RequestHeader(SESSION_KEY) String sessionKey,
            @RequestHeader(TENANT_ID) String tenantId,
            @RequestHeader(USER_ID) String userId,
            @RequestHeader(CONFIRMATION_SIGNATURE) String signature,
            @Valid @RequestBody AgentPurchaseConfirmationRequest request) {
        ConfirmationProof proof = proofVerifier.verify(
                tenantId, userId, sessionKey, request, signature);
        return AgentToolResponse.ok(
                requestId,
                service.confirm(
                        tenantId,
                        userId,
                        sessionKey,
                        request.getIntentId(),
                        request.getProgramId(),
                        request.getExpectedVersion(),
                        request.getQuoteHash(),
                        proof));
    }

    @ExceptionHandler(PurchaseIntentException.class)
    public AgentToolResponse<Void> handleBusinessError(
            PurchaseIntentException exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        metrics.confirmationRejected("business");
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
        metrics.confirmationRejected("validation");
        return AgentToolResponse.error(requestId, 400, "购买确认参数不合法", false);
    }

    @ExceptionHandler(Throwable.class)
    public AgentToolResponse<Void> handleUnexpectedError(
            Throwable exception,
            @RequestHeader(value = TOOL_CALL_ID, required = false) String requestId) {
        metrics.confirmationRejected("unexpected");
        log.error("Purchase confirmation failed, requestId={}", requestId, exception);
        return AgentToolResponse.error(requestId, -100, "购买确认服务暂时不可用", true);
    }
}
