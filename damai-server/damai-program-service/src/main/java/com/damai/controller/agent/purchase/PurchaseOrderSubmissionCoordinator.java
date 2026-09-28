package com.damai.controller.agent.purchase;

import com.damai.controller.agent.dto.AgentPurchaseIntentGetRequest;
import com.damai.controller.agent.dto.AgentPurchaseOrderSubmitRequest;
import com.damai.controller.agent.vo.AgentPurchaseIntentVo;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Duration;

/** Fast-path submitter; the scheduled worker owns all unfinished recovery. */
@Service
public class PurchaseOrderSubmissionCoordinator {

    private final PurchaseOrderSubmissionStore store;
    private final PurchaseOrderSubmissionProcessor processor;
    private final PurchaseIntentService intentService;
    private final Duration leaseDuration;

    public PurchaseOrderSubmissionCoordinator(
            PurchaseOrderSubmissionStore store,
            PurchaseOrderSubmissionProcessor processor,
            PurchaseIntentService intentService,
            @Value("${agent.purchase-intents.order-lease-seconds:30}") long leaseSeconds) {
        this.store = store;
        this.processor = processor;
        this.intentService = intentService;
        this.leaseDuration = Duration.ofSeconds(Math.max(15, leaseSeconds));
    }

    public AgentPurchaseIntentVo submit(
            String tenantId,
            String userId,
            String sessionKey,
            String requestId,
            AgentPurchaseOrderSubmitRequest request) {
        PurchaseOrderSubmission submission = store.begin(
                tenantId, userId, sessionKey, request);
        PurchaseOrderSubmission claimed = store.claimOne(
                submission, owner(requestId), leaseDuration);
        if (claimed != null) {
            processor.process(claimed);
        }
        AgentPurchaseIntentGetRequest query = new AgentPurchaseIntentGetRequest();
        query.setIntentId(request.getIntentId());
        query.setProgramId(request.getProgramId());
        return intentService.get(tenantId, userId, query);
    }

    private String owner(String requestId) {
        String normalized = requestId == null || requestId.isBlank()
                ? "tool"
                : "tool-" + requestId.trim();
        return normalized.length() <= 128 ? normalized : normalized.substring(0, 128);
    }
}
