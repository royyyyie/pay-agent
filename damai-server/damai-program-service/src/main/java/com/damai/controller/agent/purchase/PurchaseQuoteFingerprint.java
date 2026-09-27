package com.damai.controller.agent.purchase;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Date;
import java.util.HexFormat;

/** Deterministically binds an intent to its trusted owner and immutable quote fields. */
public final class PurchaseQuoteFingerprint {

    private PurchaseQuoteFingerprint() {
    }

    public static String create(PurchaseIntent intent) {
        return sha256(String.join(
                "|",
                intent.getTenantId(),
                intent.getUserId(),
                intent.getSessionKey(),
                String.valueOf(intent.getId()),
                String.valueOf(intent.getProgramId()),
                String.valueOf(intent.getTicketCategoryId()),
                String.valueOf(intent.getQuantity()),
                String.valueOf(intent.getUnitAmountFen()),
                String.valueOf(intent.getTotalAmountFen()),
                intent.getCurrency(),
                intent.getQuoteExpiresAt().toInstant().toString()));
    }

    public static String proofHash(String canonicalProof) {
        return sha256(canonicalProof);
    }

    public static String nonceHash(String nonce) {
        return sha256("confirmation-nonce|" + nonce);
    }

    public static boolean equals(String left, String right) {
        return left != null
                && right != null
                && MessageDigest.isEqual(
                        left.getBytes(StandardCharsets.US_ASCII),
                        right.getBytes(StandardCharsets.US_ASCII));
    }

    static String canonicalProof(
            String tenantId,
            String userId,
            String sessionKey,
            Long intentId,
            Long programId,
            Long expectedVersion,
            String quoteHash,
            Instant confirmedAt,
            String nonce) {
        return String.join(
                "\n",
                tenantId,
                userId,
                sessionKey,
                String.valueOf(intentId),
                String.valueOf(programId),
                String.valueOf(expectedVersion),
                quoteHash,
                confirmedAt.toString(),
                nonce);
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }
}
