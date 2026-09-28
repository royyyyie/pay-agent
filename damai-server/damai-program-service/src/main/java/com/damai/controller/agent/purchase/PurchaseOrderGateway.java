package com.damai.controller.agent.purchase;

import java.util.Optional;

/** Adapter boundary around the existing Program/Order state machines. */
public interface PurchaseOrderGateway {

    Optional<PurchaseOrderFact> find(Long orderNumber);

    Long create(PurchaseOrderSubmission submission);
}
