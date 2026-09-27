package com.damai.controller.agent.purchase;

public class PurchaseIntentException extends RuntimeException {

    private final int code;

    public PurchaseIntentException(int code, String message) {
        super(message);
        this.code = code;
    }

    public int getCode() {
        return code;
    }
}
