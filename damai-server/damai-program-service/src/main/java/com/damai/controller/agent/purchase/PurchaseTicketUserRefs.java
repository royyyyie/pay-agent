package com.damai.controller.agent.purchase;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.stream.Collectors;

/** Canonical, PII-free persistence format for Java-managed ticket-user references. */
public final class PurchaseTicketUserRefs {

    private PurchaseTicketUserRefs() {
    }

    public static String canonical(List<Long> values, int quantity) {
        if (values == null
                || values.size() != quantity
                || values.isEmpty()
                || values.size() > 6
                || values.stream().anyMatch(value -> value == null || value <= 0)
                || new LinkedHashSet<>(values).size() != values.size()) {
            throw new PurchaseIntentException(422, "购票人引用必须有效、唯一且与数量一致");
        }
        return values.stream().map(String::valueOf).collect(Collectors.joining(","));
    }

    public static List<Long> parse(String canonical) {
        if (canonical == null || canonical.isBlank()) {
            throw new PurchaseIntentException(409, "购买意向尚未绑定购票人引用");
        }
        try {
            List<Long> values = Arrays.stream(canonical.split(",", -1))
                    .map(Long::valueOf)
                    .toList();
            canonical(values, values.size());
            return values;
        } catch (NumberFormatException exception) {
            throw new PurchaseIntentException(409, "购买意向购票人引用损坏");
        }
    }
}
