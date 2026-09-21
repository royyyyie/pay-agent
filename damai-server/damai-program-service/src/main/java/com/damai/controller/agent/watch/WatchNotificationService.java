package com.damai.controller.agent.watch;

import com.baidu.fsg.uid.UidGenerator;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Date;
import java.util.stream.Collectors;

/** Idempotent IN_APP sink. Identity is resolved locally and never travels in Kafka. */
@Service
public class WatchNotificationService {

    private final WatchRuleMapper ruleMapper;
    private final WatchNotificationMapper notificationMapper;
    private final UidGenerator uidGenerator;

    public WatchNotificationService(
            WatchRuleMapper ruleMapper,
            WatchNotificationMapper notificationMapper,
            UidGenerator uidGenerator) {
        this.ruleMapper = ruleMapper;
        this.notificationMapper = notificationMapper;
        this.uidGenerator = uidGenerator;
    }

    @Transactional(rollbackFor = Exception.class)
    public WatchNotificationConsumeResult consume(WatchNotificationEvent event) {
        event.validate();
        WatchRule current = ruleMapper.selectCurrent(event.ruleId(), event.programId());
        if (current == null) {
            return WatchNotificationConsumeResult.MISSING_RULE;
        }
        boolean deliver = WatchRuleState.ACTIVE.name().equals(current.getRuleState())
                && event.ruleVersion().equals(current.getVersion())
                && event.channel().equals(current.getNotificationChannel());
        WatchNotification notification = toNotification(current, event, deliver);
        if (notificationMapper.insertIdempotent(notification) == 0) {
            return WatchNotificationConsumeResult.DUPLICATE;
        }
        return deliver
                ? WatchNotificationConsumeResult.DELIVERED
                : WatchNotificationConsumeResult.SUPPRESSED;
    }

    private WatchNotification toNotification(
            WatchRule rule, WatchNotificationEvent event, boolean deliver) {
        WatchNotification notification = new WatchNotification();
        notification.setId(uidGenerator.getUid());
        notification.setEventId(event.eventId());
        notification.setRuleId(event.ruleId());
        notification.setProgramId(event.programId());
        notification.setRuleVersion(event.ruleVersion());
        notification.setTenantId(rule.getTenantId());
        notification.setUserId(rule.getUserId());
        notification.setChannel(event.channel());
        notification.setDeliveryState(deliver
                ? WatchNotificationDeliveryState.DELIVERED.name()
                : WatchNotificationDeliveryState.SUPPRESSED.name());
        notification.setTitle("监控节目 " + event.programId() + " 已有符合条件的票档");
        notification.setContent(content(event));
        notification.setMatchedTicketCategoryIds(event.matchedTicketCategoryIds().stream()
                .map(String::valueOf)
                .collect(Collectors.joining(",")));
        notification.setMatchedCategoryCount(event.matchedCategoryCount());
        notification.setMatchedRemaining(event.matchedRemaining());
        notification.setMinimumPrice(event.minimumPrice());
        notification.setFreshnessAt(Date.from(Instant.parse(event.freshnessAt())));
        notification.setCreateTime(new Date());
        return notification;
    }

    private String content(WatchNotificationEvent event) {
        return "节目 "
                + event.programId()
                + " 匹配票档 "
                + event.matchedTicketCategoryIds()
                + "，最低价格 "
                + event.minimumPrice().stripTrailingZeros().toPlainString()
                + "，匹配余量 "
                + event.matchedRemaining()
                + "，库存时间 "
                + event.freshnessAt();
    }
}
