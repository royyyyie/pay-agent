package com.damai.controller.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AgentToolWatchDelegationFilterTest {

    private static final String SECRET = "delegation-contract-secret-value-32";
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final AgentWatchDelegationFilter filter =
            new AgentWatchDelegationFilter(objectMapper, SECRET);

    @Test
    void acceptsBoundReversibleWriteDelegation() throws Exception {
        MockHttpServletRequest request = request("/internal/agent/v1/tools/watch-rules/create");
        sign(request, "tenant-1", "REVERSIBLE_WRITE", "watch:write");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean invoked = new AtomicBoolean(false);

        filter.doFilter(request, response, (servletRequest, servletResponse) -> invoked.set(true));

        assertTrue(invoked.get());
    }

    @Test
    void rejectsDelegationReplayedForAnotherOwner() throws Exception {
        MockHttpServletRequest request = request("/internal/agent/v1/tools/watch-rules/create");
        sign(request, "another-tenant", "REVERSIBLE_WRITE", "watch:write");
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicBoolean invoked = new AtomicBoolean(false);

        filter.doFilter(request, response, (servletRequest, servletResponse) -> invoked.set(true));

        assertFalse(invoked.get());
        assertEquals(401, response.getStatus());
    }

    @Test
    void rejectsReadOnlyDelegationForWriteButAllowsOwnerList() throws Exception {
        MockHttpServletRequest write = request("/internal/agent/v1/tools/watch-rules/status");
        sign(write, "tenant-1", "READ_ONLY", "watch:write");
        MockHttpServletResponse rejected = new MockHttpServletResponse();

        filter.doFilter(write, rejected, (servletRequest, servletResponse) -> {});

        assertEquals(401, rejected.getStatus());

        MockHttpServletRequest list = request("/internal/agent/v1/tools/watch-rules/list");
        sign(list, "tenant-1", "READ_ONLY", "watch:read");
        MockHttpServletResponse accepted = new MockHttpServletResponse();
        AtomicBoolean invoked = new AtomicBoolean(false);
        filter.doFilter(list, accepted, (servletRequest, servletResponse) -> invoked.set(true));
        assertTrue(invoked.get());
    }

    private MockHttpServletRequest request(String path) {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", path);
        request.addHeader("X-Agent-Tool-Call-Id", "call-1");
        request.addHeader("X-Agent-Turn-Id", "turn-1");
        request.addHeader("X-Agent-Session-Key", "session-1");
        request.addHeader("X-Agent-Tenant-Id", "tenant-1");
        request.addHeader("X-Agent-User-Id", "user-1");
        return request;
    }

    private void sign(
            MockHttpServletRequest request,
            String tenantId,
            String riskCeiling,
            String scope) throws Exception {
        long now = Instant.now().getEpochSecond();
        ObjectNode claims = objectMapper.createObjectNode();
        claims.put("tenantId", tenantId);
        claims.put("userId", "user-1");
        claims.put("sessionKey", "session-1");
        claims.put("turnId", "turn-1");
        claims.put("issuedAt", now);
        claims.put("expiresAt", now + 60);
        claims.put("riskCeiling", riskCeiling);
        claims.putArray("toolScopes").add(scope);
        String encoded = Base64.getUrlEncoder()
                .withoutPadding()
                .encodeToString(objectMapper.writeValueAsBytes(claims));
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        String signature = HexFormat.of().formatHex(
                mac.doFinal(encoded.getBytes(StandardCharsets.US_ASCII)));
        request.addHeader("X-Agent-Delegation", encoded);
        request.addHeader("X-Agent-Delegation-Signature", signature);
    }
}
