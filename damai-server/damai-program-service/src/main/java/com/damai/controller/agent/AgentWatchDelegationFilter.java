package com.damai.controller.agent;

import com.alibaba.fastjson.JSON;
import com.damai.controller.agent.vo.AgentToolResponse;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.StreamSupport;

/**
 * Re-verifies the original Java-BFF delegation before owner-scoped read or write operations.
 * The internal API key authenticates Python as a service; this filter independently authenticates
 * the end-user authority, scope, risk ceiling, expiry, and binding to the current request.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 2)
public class AgentWatchDelegationFilter extends OncePerRequestFilter {

    static final String DELEGATION_HEADER = "X-Agent-Delegation";
    static final String SIGNATURE_HEADER = "X-Agent-Delegation-Signature";

    private static final Pattern ENCODED_PATTERN = Pattern.compile("^[A-Za-z0-9_-]+$");
    private static final Pattern SIGNATURE_PATTERN = Pattern.compile("^[0-9a-f]{64}$");
    private static final int MAX_DELEGATION_LENGTH = 4096;
    private static final long MAX_DELEGATION_SECONDS = 300;

    private final ObjectMapper objectMapper;
    private final String delegationHmacKey;

    public AgentWatchDelegationFilter(
            ObjectMapper objectMapper,
            @Value("${AGENT_DELEGATION_HMAC_KEY:}") String delegationHmacKey) {
        this.objectMapper = objectMapper;
        this.delegationHmacKey = delegationHmacKey;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith(
                AgentToolRequestContextFilter.WATCH_RULE_PATH_PREFIX);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        if (!hasValidDelegation(request)) {
            writeUnauthorized(response, request.getHeader(
                    AgentToolRequestContextFilter.TOOL_CALL_ID_HEADER));
            return;
        }
        filterChain.doFilter(request, response);
    }

    private boolean hasValidDelegation(HttpServletRequest request) {
        String encoded = request.getHeader(DELEGATION_HEADER);
        String signature = request.getHeader(SIGNATURE_HEADER);
        if (delegationHmacKey == null
                || delegationHmacKey.length() < 32
                || encoded == null
                || encoded.length() > MAX_DELEGATION_LENGTH
                || !ENCODED_PATTERN.matcher(encoded).matches()
                || signature == null
                || !SIGNATURE_PATTERN.matcher(signature).matches()
                || !validSignature(encoded, signature)) {
            return false;
        }
        try {
            JsonNode claims = objectMapper.readTree(Base64.getUrlDecoder().decode(pad(encoded)));
            long now = Instant.now().getEpochSecond();
            long issuedAt = claims.path("issuedAt").asLong(Long.MIN_VALUE);
            long expiresAt = claims.path("expiresAt").asLong(Long.MIN_VALUE);
            boolean validLifetime = issuedAt <= now + 30
                    && expiresAt > now
                    && expiresAt > issuedAt
                    && expiresAt - issuedAt <= MAX_DELEGATION_SECONDS;
            boolean boundContext = constantTimeTextEquals(
                            claims.path("tenantId").asText(),
                            request.getHeader(AgentToolRequestContextFilter.TENANT_ID_HEADER))
                    && constantTimeTextEquals(
                            claims.path("userId").asText(),
                            request.getHeader(AgentToolRequestContextFilter.USER_ID_HEADER))
                    && constantTimeTextEquals(
                            claims.path("sessionKey").asText(),
                            request.getHeader(AgentToolRequestContextFilter.SESSION_KEY_HEADER))
                    && constantTimeTextEquals(
                            claims.path("turnId").asText(),
                            request.getHeader(AgentToolRequestContextFilter.TURN_ID_HEADER));
            boolean writeOperation = !request.getRequestURI().endsWith("/list");
            String requiredScope = writeOperation ? "watch:write" : "watch:read";
            boolean hasScope = claims.path("toolScopes").isArray()
                    && StreamSupport.stream(claims.path("toolScopes").spliterator(), false)
                            .map(JsonNode::asText)
                            .anyMatch(requiredScope::equals);
            String riskCeiling = claims.path("riskCeiling").asText();
            boolean riskAllowed = writeOperation
                    ? "REVERSIBLE_WRITE".equals(riskCeiling)
                    : ("READ_ONLY".equals(riskCeiling)
                            || "REVERSIBLE_WRITE".equals(riskCeiling));
            return validLifetime && boundContext && hasScope && riskAllowed;
        } catch (IllegalArgumentException | IOException exception) {
            return false;
        }
    }

    private boolean validSignature(String encoded, String signature) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(
                    delegationHmacKey.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] expected = mac.doFinal(encoded.getBytes(StandardCharsets.US_ASCII));
            return MessageDigest.isEqual(expected, hexToBytes(signature));
        } catch (GeneralSecurityException | IllegalArgumentException exception) {
            return false;
        }
    }

    private boolean constantTimeTextEquals(String expected, String actual) {
        return actual != null && MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                actual.getBytes(StandardCharsets.UTF_8));
    }

    private String pad(String encoded) {
        return encoded + "=".repeat((4 - encoded.length() % 4) % 4);
    }

    private byte[] hexToBytes(String value) {
        byte[] decoded = new byte[value.length() / 2];
        for (int index = 0; index < decoded.length; index++) {
            int offset = index * 2;
            decoded[index] = (byte) Integer.parseInt(value.substring(offset, offset + 2), 16);
        }
        return decoded;
    }

    private void writeUnauthorized(HttpServletResponse response, String toolCallId)
            throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        AgentToolResponse<Void> body = AgentToolResponse.error(
                Optional.ofNullable(toolCallId)
                        .filter(value -> !value.isBlank())
                        .orElseGet(() -> UUID.randomUUID().toString()),
                401,
                "Agent 监控委托身份无效",
                false);
        response.getWriter().write(JSON.toJSONString(body));
    }
}
