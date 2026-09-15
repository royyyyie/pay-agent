package com.damai.controller.agent;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AgentToolRequestContextFilterTest {

    private static final String TRACEPARENT =
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    private final AgentToolRequestContextFilter filter = new AgentToolRequestContextFilter();

    @Test
    void rejectsMissingTrustedContextBeforeControllerExecution() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest(
                "POST", "/internal/agent/v1/tools/programs/search");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean invoked = new AtomicBoolean(false);
        FilterChain chain = (servletRequest, servletResponse) -> invoked.set(true);

        filter.doFilter(request, response, chain);

        assertEquals(400, response.getStatus());
        assertFalse(invoked.get());
        assertTrue(response.getContentAsString().contains("Agent 请求上下文不合法"));
    }

    @Test
    void propagatesValidW3cTraceContextAndCleansMdc() throws Exception {
        MockHttpServletRequest request = validRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean invoked = new AtomicBoolean(false);
        FilterChain chain = (servletRequest, servletResponse) -> {
            invoked.set(true);
            assertEquals("4bf92f3577b34da6a3ce929d0e0e4736", MDC.get("traceId"));
            assertEquals("turn-contract-test", MDC.get("agentTurnId"));
        };

        filter.doFilter(request, response, chain);

        assertTrue(invoked.get());
        assertEquals(TRACEPARENT, response.getHeader("traceparent"));
        assertNull(MDC.get("traceId"));
        assertNull(MDC.get("agentTurnId"));
        assertNull(MDC.get("agentToolCallId"));
    }

    @Test
    void rejectsAllZeroW3cTraceId() throws Exception {
        MockHttpServletRequest request = validRequest();
        request.removeHeader("traceparent");
        request.addHeader(
                "traceparent",
                "00-00000000000000000000000000000000-00f067aa0ba902b7-01");
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, (servletRequest, servletResponse) -> {});

        assertEquals(400, response.getStatus());
    }

    private MockHttpServletRequest validRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest(
                "POST", "/internal/agent/v1/tools/programs/search");
        request.addHeader("X-Agent-Tool-Call-Id", "call-contract-test");
        request.addHeader("X-Agent-Turn-Id", "turn-contract-test");
        request.addHeader("X-Agent-Session-Key", "session-contract-test");
        request.addHeader("traceparent", TRACEPARENT);
        return request;
    }
}
