package com.damai.controller.agent.watch;

/** Safe business error returned through the stable Agent Tool envelope. */
public class WatchRuleException extends RuntimeException {

    private final int code;

    public WatchRuleException(int code, String message) {
        super(message);
        this.code = code;
    }

    public int getCode() {
        return code;
    }
}
