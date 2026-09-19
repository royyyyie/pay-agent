package com.damai.controller.agent;

import com.alibaba.fastjson.JSON;
import com.damai.controller.agent.vo.AgentToolResponse;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Validates the trusted Agent request context and exposes W3C trace identifiers to logs.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
@Slf4j
public class AgentToolRequestContextFilter extends OncePerRequestFilter {

    static final String TOOL_PATH_PREFIX = "/internal/agent/";
    static final String TOOL_CALL_ID_HEADER = "X-Agent-Tool-Call-Id";
    static final String TURN_ID_HEADER = "X-Agent-Turn-Id";
    static final String SESSION_KEY_HEADER = "X-Agent-Session-Key";
    static final String TENANT_ID_HEADER = "X-Agent-Tenant-Id";
    static final String USER_ID_HEADER = "X-Agent-User-Id";
    static final String TRACEPARENT_HEADER = "traceparent";
    static final String WATCH_RULE_PATH_PREFIX = "/internal/agent/v1/tools/watch-rules/";

    private static final int MAX_CONTEXT_ID_LENGTH = 128;
    private static final Pattern TRACEPARENT_PATTERN = Pattern.compile(
            "^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$");
    private static final String ZERO_TRACE_ID = "00000000000000000000000000000000";
    private static final String ZERO_SPAN_ID = "0000000000000000";

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith(TOOL_PATH_PREFIX);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        String toolCallId = request.getHeader(TOOL_CALL_ID_HEADER);
        String turnId = request.getHeader(TURN_ID_HEADER);
        String sessionKey = request.getHeader(SESSION_KEY_HEADER);
        String tenantId = request.getHeader(TENANT_ID_HEADER);
        String userId = request.getHeader(USER_ID_HEADER);
        String traceparent = request.getHeader(TRACEPARENT_HEADER);
        Matcher traceparentMatcher = traceparent == null
                ? null
                : TRACEPARENT_PATTERN.matcher(traceparent);

        if (!isValidContextId(toolCallId)
                || !isValidContextId(turnId)
                || !isValidContextId(sessionKey)
                || (request.getRequestURI().startsWith(WATCH_RULE_PATH_PREFIX)
                        && (!isValidOwnerId(tenantId) || !isValidOwnerId(userId)))
                || !isValidTraceparent(traceparentMatcher)) {
            writeBadRequest(response, toolCallId);
            return;
        }

        String traceId = traceparentMatcher.group(1);
        MDC.put("traceId", traceId);
        MDC.put("agentTurnId", turnId);
        MDC.put("agentToolCallId", toolCallId);
        response.setHeader(TRACEPARENT_HEADER, traceparent);
        long startedAt = System.nanoTime();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long durationMs = (System.nanoTime() - startedAt) / 1_000_000;
            log.info(
                    "Agent tool request completed, method={}, path={}, status={}, durationMs={}",
                    request.getMethod(),
                    request.getRequestURI(),
                    response.getStatus(),
                    durationMs);
            MDC.remove("traceId");
            MDC.remove("agentTurnId");
            MDC.remove("agentToolCallId");
        }
    }

    private boolean isValidContextId(String value) {
        return value != null && !value.isBlank() && value.length() <= MAX_CONTEXT_ID_LENGTH;
    }

    private boolean isValidOwnerId(String value) {
        return value != null && !value.isBlank() && value.length() <= 200;
    }

    private boolean isValidTraceparent(Matcher matcher) {
        return matcher != null
                && matcher.matches()
                && !ZERO_TRACE_ID.equals(matcher.group(1))
                && !ZERO_SPAN_ID.equals(matcher.group(2));
    }

    private void writeBadRequest(HttpServletResponse response, String toolCallId) throws IOException {
        response.setStatus(HttpServletResponse.SC_BAD_REQUEST);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        AgentToolResponse<Void> body = AgentToolResponse.error(
                Optional.ofNullable(toolCallId)
                        .filter(value -> !value.isBlank())
                        .orElseGet(() -> UUID.randomUUID().toString()),
                400,
                "Agent 请求上下文不合法",
                false);
        response.getWriter().write(JSON.toJSONString(body));
    }
}
