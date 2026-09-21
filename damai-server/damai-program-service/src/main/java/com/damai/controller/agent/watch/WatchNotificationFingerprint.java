package com.damai.controller.agent.watch;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Date;
import java.util.HexFormat;

/** Stable condition and notification-window fingerprints. */
public final class WatchNotificationFingerprint {

    private WatchNotificationFingerprint() {
    }

    public static String condition(WatchRule rule) {
        return sha256(String.join(
                "|",
                value(rule.getTicketCategoryIds()),
                decimal(rule.getMaxPrice()),
                String.valueOf(rule.getMinRemaining()),
                value(rule.getNotificationChannel())));
    }

    public static Date windowStart(Date checkedAt, Duration cooldown) {
        long windowMillis = Math.max(1, cooldown.toMillis());
        long epochMillis = checkedAt.getTime();
        return new Date(epochMillis - Math.floorMod(epochMillis, windowMillis));
    }

    public static String dedupeKey(
            WatchRule rule, String conditionFingerprint, Date windowStart) {
        return sha256(rule.getId()
                + "|"
                + rule.getClaimedVersion()
                + "|"
                + conditionFingerprint
                + "|"
                + windowStart.getTime());
    }

    static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String decimal(BigDecimal value) {
        return value == null ? "" : value.stripTrailingZeros().toPlainString();
    }

    private static String value(String value) {
        return value == null ? "" : value;
    }
}
