package com.damai.controller.agent.purchase;

public class PurchaseOrderGatewayException extends RuntimeException {

    private final String code;
    private final boolean retryable;

    public PurchaseOrderGatewayException(String code, boolean retryable, Throwable cause) {
        super(code, cause);
        this.code = code;
        this.retryable = retryable;
    }

    public String code() {
        return code;
    }

    public boolean retryable() {
        return retryable;
    }
}
