package com.damai.controller.agent.purchase;

import com.damai.controller.agent.dto.AgentPurchaseConfirmationRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Date;
import java.util.HexFormat;
import java.util.regex.Pattern;

/** Verifies explicit BFF confirmation independently from the Python service credential. */
@Component
public class ConfirmationProofVerifier {

    private static final Pattern SIGNATURE_PATTERN = Pattern.compile("^[0-9a-f]{64}$");

    private final String secret;
    private final Duration maximumClockSkew;
    private final Clock clock;

    public ConfirmationProofVerifier(
            @Value("${AGENT_CONFIRMATION_HMAC_KEY:}") String secret,
            @Value("${agent.purchase-intents.confirmation-clock-skew-seconds:120}")
                    long maximumClockSkewSeconds) {
        this(secret, Duration.ofSeconds(Math.max(30, maximumClockSkewSeconds)), Clock.systemUTC());
    }

    ConfirmationProofVerifier(String secret, Duration maximumClockSkew, Clock clock) {
        this.secret = secret;
        this.maximumClockSkew = maximumClockSkew;
        this.clock = clock;
    }

    public ConfirmationProof verify(
            String tenantId,
            String userId,
            String sessionKey,
            AgentPurchaseConfirmationRequest request,
            String signature) {
        if (secret == null
                || secret.length() < 32
                || signature == null
                || !SIGNATURE_PATTERN.matcher(signature).matches()) {
            throw invalidProof();
        }
        final Instant confirmedInstant;
        try {
            confirmedInstant = Instant.parse(request.getConfirmedAt());
        } catch (DateTimeParseException exception) {
            throw invalidProof();
        }
        Instant now = clock.instant();
        if (confirmedInstant.isBefore(now.minus(maximumClockSkew))
                || confirmedInstant.isAfter(now.plusSeconds(30))) {
            throw invalidProof();
        }
        Date confirmedAt = Date.from(confirmedInstant);
        String canonical = PurchaseQuoteFingerprint.canonicalProof(
                tenantId,
                userId,
                sessionKey,
                request.getIntentId(),
                request.getProgramId(),
                request.getExpectedVersion(),
                request.getQuoteHash(),
                confirmedInstant,
                request.getConfirmationNonce());
        byte[] expected = sign(canonical);
        byte[] supplied;
        try {
            supplied = HexFormat.of().parseHex(signature);
        } catch (IllegalArgumentException exception) {
            throw invalidProof();
        }
        if (!MessageDigest.isEqual(expected, supplied)) {
            throw invalidProof();
        }
        return new ConfirmationProof(
                PurchaseQuoteFingerprint.proofHash(canonical),
                PurchaseQuoteFingerprint.nonceHash(request.getConfirmationNonce()),
                confirmedAt);
    }

    private byte[] sign(String canonical) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(canonical.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("confirmation HMAC unavailable", exception);
        }
    }

    private PurchaseIntentException invalidProof() {
        return new PurchaseIntentException(401, "购买确认凭据无效或已过期");
    }
}
