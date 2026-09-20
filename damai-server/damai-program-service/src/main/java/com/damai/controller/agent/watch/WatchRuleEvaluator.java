package com.damai.controller.agent.watch;

import com.damai.vo.TicketCategoryDetailVo;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/** Pure evaluator for ticket-category allowlists, price ceilings and remaining inventory. */
public final class WatchRuleEvaluator {

    private WatchRuleEvaluator() {
    }

    public static WatchEvaluation evaluate(
            WatchRule rule, List<TicketCategoryDetailVo> ticketCategories) {
        Set<Long> allowlist = parseAllowlist(rule.getTicketCategoryIds());
        long minimumRemaining = rule.getMinRemaining() == null ? 1 : rule.getMinRemaining();
        List<TicketCategoryDetailVo> matches = ticketCategories == null
                ? List.of()
                : ticketCategories.stream()
                        .filter(Objects::nonNull)
                        .filter(ticket -> ticket.getId() != null)
                        .filter(ticket -> allowlist.isEmpty() || allowlist.contains(ticket.getId()))
                        .filter(ticket -> ticket.getPrice() != null)
                        .filter(ticket -> ticket.getRemainNumber() != null)
                        .filter(ticket -> ticket.getRemainNumber() >= minimumRemaining)
                        .filter(ticket -> rule.getMaxPrice() == null
                                || ticket.getPrice().compareTo(rule.getMaxPrice()) <= 0)
                        .sorted(Comparator.comparing(TicketCategoryDetailVo::getId))
                        .toList();
        if (matches.isEmpty()) {
            return new WatchEvaluation(WatchCheckOutcome.NO_MATCH, null, 0, 0, null, null);
        }
        long remaining = 0;
        for (TicketCategoryDetailVo match : matches) {
            remaining = saturatedAdd(remaining, match.getRemainNumber());
        }
        BigDecimal minimumPrice = matches.stream()
                .map(TicketCategoryDetailVo::getPrice)
                .min(Comparator.naturalOrder())
                .orElseThrow();
        String ids = matches.stream()
                .map(TicketCategoryDetailVo::getId)
                .map(String::valueOf)
                .collect(Collectors.joining(","));
        return new WatchEvaluation(
                WatchCheckOutcome.MATCHED, ids, matches.size(), remaining, minimumPrice, null);
    }

    private static Set<Long> parseAllowlist(String serialized) {
        if (serialized == null || serialized.isBlank()) {
            return Set.of();
        }
        return Arrays.stream(serialized.split(","))
                .map(String::trim)
                .filter(value -> !value.isEmpty())
                .map(Long::valueOf)
                .collect(Collectors.toCollection(HashSet::new));
    }

    private static long saturatedAdd(long left, long right) {
        if (right > 0 && Long.MAX_VALUE - left < right) {
            return Long.MAX_VALUE;
        }
        return left + right;
    }
}
