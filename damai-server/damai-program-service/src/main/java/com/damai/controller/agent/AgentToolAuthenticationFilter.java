package com.damai.controller.agent;

import com.alibaba.fastjson.JSON;
import com.damai.controller.agent.vo.AgentToolResponse;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Optional;
import java.util.UUID;

/**
 * 保护内部 Agent 工具接口，避免它们被当作普通公开业务接口调用。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class AgentToolAuthenticationFilter extends OncePerRequestFilter {

    private static final String TOOL_PATH_PREFIX = "/internal/agent/";
    private static final String API_KEY_HEADER = "X-Agent-Key";
    private static final String REQUEST_ID_HEADER = "X-Agent-Tool-Call-Id";

    @Value("${AGENT_TOOL_API_KEY:change-me-local}")
    private String expectedApiKey;

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith(TOOL_PATH_PREFIX);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        String actualApiKey = request.getHeader(API_KEY_HEADER);
        if (!constantTimeEquals(expectedApiKey, actualApiKey)) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            AgentToolResponse<Void> body = AgentToolResponse.error(
                    Optional.ofNullable(request.getHeader(REQUEST_ID_HEADER))
                            .filter(value -> !value.isBlank())
                            .orElseGet(() -> UUID.randomUUID().toString()),
                    401,
                    "Agent 工具鉴权失败",
                    false);
            response.getWriter().write(JSON.toJSONString(body));
            return;
        }
        filterChain.doFilter(request, response);
    }

    private boolean constantTimeEquals(String expected, String actual) {
        if (expected == null || actual == null) {
            return false;
        }
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                actual.getBytes(StandardCharsets.UTF_8));
    }
}
