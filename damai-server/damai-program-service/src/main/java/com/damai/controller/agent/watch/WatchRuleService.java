package com.damai.controller.agent.watch;

import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.damai.controller.agent.dto.AgentWatchRuleCreateRequest;
import com.damai.controller.agent.dto.AgentWatchRuleListRequest;
import com.damai.controller.agent.dto.AgentWatchRuleStatusRequest;
import com.damai.controller.agent.dto.AgentWatchRuleUpdateRequest;
import com.damai.controller.agent.vo.AgentWatchRulePageVo;
import com.damai.controller.agent.vo.AgentWatchRuleVo;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;

/**
 * Durable, owner-scoped watch-rule control plane.
 *
 * Scheduling and notification delivery consume this state in later phase-5 batches. All writes
 * are idempotent or optimistic and always include the immutable program sharding key.
 */
@Service
public class WatchRuleService {

    static final int MAX_ACTIVE_RULES_PER_USER = 20;

    private final WatchRuleMapper watchRuleMapper;
    private final UidGenerator uidGenerator;

    public WatchRuleService(WatchRuleMapper watchRuleMapper, UidGenerator uidGenerator) {
        this.watchRuleMapper = watchRuleMapper;
        this.uidGenerator = uidGenerator;
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentWatchRuleVo create(
            String tenantId,
            String userId,
            String idempotencyKey,
            AgentWatchRuleCreateRequest request) {
        WatchRule existing = selectByIdempotency(
                tenantId, userId, request.getProgramId(), idempotencyKey);
        if (existing != null) {
            return toVo(existing);
        }
        enforceActiveRuleLimit(tenantId, userId);

        Date now = new Date();
        WatchRule rule = new WatchRule();
        rule.setId(uidGenerator.getUid());
        rule.setTenantId(tenantId);
        rule.setUserId(userId);
        rule.setIdempotencyKey(idempotencyKey);
        rule.setProgramId(request.getProgramId());
        rule.setName(request.getName() == null
                ? "节目 " + request.getProgramId() + " 票务监控"
                : request.getName());
        rule.setTicketCategoryIds(serializeTicketCategoryIds(request.getTicketCategoryIds()));
        rule.setMaxPrice(request.getMaxPrice());
        rule.setMinRemaining(request.getMinRemaining());
        rule.setCheckIntervalSeconds(request.getCheckIntervalSeconds());
        rule.setNotificationChannel(request.getNotificationChannel().name());
        rule.setRuleState(WatchRuleState.ACTIVE.name());
        rule.setNextCheckTime(now);
        rule.setVersion(1L);
        rule.setCreateTime(now);
        rule.setEditTime(now);

        int inserted = watchRuleMapper.insertIdempotent(rule);
        if (inserted == 1) {
            return toVo(rule);
        }
        WatchRule winner = selectByIdempotency(
                tenantId, userId, request.getProgramId(), idempotencyKey);
        if (winner == null) {
            throw new WatchRuleException(409, "监控规则幂等创建冲突，请重新查询");
        }
        return toVo(winner);
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentWatchRuleVo update(
            String tenantId, String userId, AgentWatchRuleUpdateRequest request) {
        WatchRule current = selectOwned(
                tenantId, userId, request.getProgramId(), request.getRuleId());
        requireVersion(current, request.getExpectedVersion());
        Date now = new Date();
        long nextVersion = current.getVersion() + 1;
        LambdaUpdateWrapper<WatchRule> update = ownedVersionUpdate(
                        tenantId,
                        userId,
                        request.getProgramId(),
                        request.getRuleId(),
                        request.getExpectedVersion())
                .set(request.getName() != null, WatchRule::getName, request.getName())
                .set(
                        request.getTicketCategoryIds() != null,
                        WatchRule::getTicketCategoryIds,
                        serializeTicketCategoryIds(request.getTicketCategoryIds()))
                .set(request.getMaxPrice() != null, WatchRule::getMaxPrice, request.getMaxPrice())
                .set(
                        Boolean.TRUE.equals(request.getClearMaxPrice()),
                        WatchRule::getMaxPrice,
                        null)
                .set(
                        request.getMinRemaining() != null,
                        WatchRule::getMinRemaining,
                        request.getMinRemaining())
                .set(
                        request.getCheckIntervalSeconds() != null,
                        WatchRule::getCheckIntervalSeconds,
                        request.getCheckIntervalSeconds())
                .set(
                        request.getCheckIntervalSeconds() != null
                                && WatchRuleState.ACTIVE.name().equals(current.getRuleState()),
                        WatchRule::getNextCheckTime,
                        now)
                .set(WatchRule::getVersion, nextVersion)
                .set(WatchRule::getEditTime, now);
        if (watchRuleMapper.update(null, update) != 1) {
            throw versionConflict();
        }
        return toVo(requireOwned(
                tenantId, userId, request.getProgramId(), request.getRuleId()));
    }

    @Transactional(rollbackFor = Exception.class)
    public AgentWatchRuleVo setStatus(
            String tenantId, String userId, AgentWatchRuleStatusRequest request) {
        WatchRule current = selectOwned(
                tenantId, userId, request.getProgramId(), request.getRuleId());
        if (current == null) {
            throw notFound();
        }
        if (request.getTargetStatus().name().equals(current.getRuleState())) {
            return toVo(current);
        }
        requireVersion(current, request.getExpectedVersion());
        if (request.getTargetStatus() == WatchRuleState.ACTIVE) {
            enforceActiveRuleLimit(tenantId, userId);
        }
        Date now = new Date();
        LambdaUpdateWrapper<WatchRule> update = ownedVersionUpdate(
                        tenantId,
                        userId,
                        request.getProgramId(),
                        request.getRuleId(),
                        request.getExpectedVersion())
                .set(WatchRule::getRuleState, request.getTargetStatus().name())
                .set(
                        request.getTargetStatus() == WatchRuleState.ACTIVE,
                        WatchRule::getNextCheckTime,
                        now)
                .set(WatchRule::getVersion, current.getVersion() + 1)
                .set(WatchRule::getEditTime, now);
        if (watchRuleMapper.update(null, update) != 1) {
            throw versionConflict();
        }
        return toVo(requireOwned(
                tenantId, userId, request.getProgramId(), request.getRuleId()));
    }

    @Transactional(readOnly = true)
    public AgentWatchRulePageVo list(
            String tenantId, String userId, AgentWatchRuleListRequest request) {
        LambdaQueryWrapper<WatchRule> query = Wrappers.lambdaQuery(WatchRule.class)
                .eq(WatchRule::getTenantId, tenantId)
                .eq(WatchRule::getUserId, userId)
                .eq(
                        request.getRuleStatus() != null,
                        WatchRule::getRuleState,
                        request.getRuleStatus() == null ? null : request.getRuleStatus().name())
                .orderByDesc(WatchRule::getCreateTime)
                .orderByDesc(WatchRule::getId);
        IPage<WatchRule> page = watchRuleMapper.selectPage(
                new Page<>(request.getPageNumber(), request.getPageSize()), query);
        return new AgentWatchRulePageVo(
                request.getPageNumber(),
                request.getPageSize(),
                page.getTotal(),
                page.getRecords().stream().map(this::toVo).toList());
    }

    private void enforceActiveRuleLimit(String tenantId, String userId) {
        Long count = watchRuleMapper.selectCount(Wrappers.lambdaQuery(WatchRule.class)
                .eq(WatchRule::getTenantId, tenantId)
                .eq(WatchRule::getUserId, userId)
                .eq(WatchRule::getRuleState, WatchRuleState.ACTIVE.name()));
        if (count != null && count >= MAX_ACTIVE_RULES_PER_USER) {
            throw new WatchRuleException(429, "当前用户的启用监控规则已达上限");
        }
    }

    private WatchRule selectByIdempotency(
            String tenantId, String userId, Long programId, String idempotencyKey) {
        return watchRuleMapper.selectOne(Wrappers.lambdaQuery(WatchRule.class)
                .eq(WatchRule::getTenantId, tenantId)
                .eq(WatchRule::getUserId, userId)
                .eq(WatchRule::getProgramId, programId)
                .eq(WatchRule::getIdempotencyKey, idempotencyKey));
    }

    private WatchRule selectOwned(String tenantId, String userId, Long programId, Long ruleId) {
        return watchRuleMapper.selectOne(Wrappers.lambdaQuery(WatchRule.class)
                .eq(WatchRule::getId, ruleId)
                .eq(WatchRule::getProgramId, programId)
                .eq(WatchRule::getTenantId, tenantId)
                .eq(WatchRule::getUserId, userId));
    }

    private WatchRule requireOwned(String tenantId, String userId, Long programId, Long ruleId) {
        WatchRule rule = selectOwned(tenantId, userId, programId, ruleId);
        if (rule == null) {
            throw notFound();
        }
        return rule;
    }

    private void requireVersion(WatchRule rule, Long expectedVersion) {
        if (rule == null) {
            throw notFound();
        }
        if (!Objects.equals(rule.getVersion(), expectedVersion)) {
            throw versionConflict();
        }
    }

    private LambdaUpdateWrapper<WatchRule> ownedVersionUpdate(
            String tenantId, String userId, Long programId, Long ruleId, Long version) {
        return Wrappers.lambdaUpdate(WatchRule.class)
                .eq(WatchRule::getId, ruleId)
                .eq(WatchRule::getProgramId, programId)
                .eq(WatchRule::getTenantId, tenantId)
                .eq(WatchRule::getUserId, userId)
                .eq(WatchRule::getVersion, version);
    }

    private WatchRuleException notFound() {
        return new WatchRuleException(404, "监控规则不存在");
    }

    private WatchRuleException versionConflict() {
        return new WatchRuleException(409, "监控规则已变化，请重新查询后再修改");
    }

    private String serializeTicketCategoryIds(List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return null;
        }
        return ids.stream()
                .filter(Objects::nonNull)
                .distinct()
                .sorted()
                .map(String::valueOf)
                .collect(Collectors.joining(","));
    }

    private List<Long> parseTicketCategoryIds(String value) {
        if (value == null || value.isBlank()) {
            return List.of();
        }
        return Arrays.stream(value.split(","))
                .map(Long::valueOf)
                .toList();
    }

    private AgentWatchRuleVo toVo(WatchRule rule) {
        AgentWatchRuleVo view = new AgentWatchRuleVo();
        view.setRuleId(rule.getId());
        view.setProgramId(rule.getProgramId());
        view.setName(rule.getName());
        view.setTicketCategoryIds(parseTicketCategoryIds(rule.getTicketCategoryIds()));
        view.setMaxPrice(rule.getMaxPrice());
        view.setMinRemaining(rule.getMinRemaining());
        view.setCheckIntervalSeconds(rule.getCheckIntervalSeconds());
        view.setNotificationChannel(rule.getNotificationChannel());
        view.setRuleStatus(rule.getRuleState());
        view.setVersion(rule.getVersion());
        view.setNextCheckAt(formatInstant(rule.getNextCheckTime()));
        view.setLastCheckedAt(formatInstant(rule.getLastCheckedTime()));
        view.setLastTriggeredAt(formatInstant(rule.getLastTriggeredTime()));
        view.setCreatedAt(formatInstant(rule.getCreateTime()));
        view.setUpdatedAt(formatInstant(rule.getEditTime()));
        return view;
    }

    private String formatInstant(Date value) {
        return value == null
                ? null
                : DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(
                        value.toInstant().atOffset(ZoneOffset.UTC));
    }
}
