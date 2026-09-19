package com.damai.controller.agent;

import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.damai.controller.agent.dto.AgentWatchRuleCreateRequest;
import com.damai.controller.agent.dto.AgentWatchRuleStatusRequest;
import com.damai.controller.agent.dto.AgentWatchRuleUpdateRequest;
import com.damai.controller.agent.vo.AgentWatchRuleVo;
import com.damai.controller.agent.watch.WatchNotificationChannel;
import com.damai.controller.agent.watch.WatchRule;
import com.damai.controller.agent.watch.WatchRuleException;
import com.damai.controller.agent.watch.WatchRuleMapper;
import com.damai.controller.agent.watch.WatchRuleService;
import com.damai.controller.agent.watch.WatchRuleState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.util.Date;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolWatchRuleServiceTest {

    private WatchRuleMapper mapper;
    private UidGenerator uidGenerator;
    private WatchRuleService service;

    @BeforeEach
    void setUp() {
        mapper = mock(WatchRuleMapper.class);
        uidGenerator = mock(UidGenerator.class);
        service = new WatchRuleService(mapper, uidGenerator);
    }

    @Test
    void createPersistsNormalizedOwnerScopedRule() {
        when(mapper.selectOne(any())).thenReturn(null);
        when(mapper.selectCount(any())).thenReturn(0L);
        when(mapper.insertIdempotent(any())).thenReturn(1);
        when(uidGenerator.getUid()).thenReturn(9001L);
        AgentWatchRuleCreateRequest request = new AgentWatchRuleCreateRequest();
        request.setProgramId(1001L);
        request.setTicketCategoryIds(List.of(8L, 3L, 8L));
        request.setMaxPrice(new BigDecimal("680.00"));
        request.setNotificationChannel(WatchNotificationChannel.IN_APP);

        AgentWatchRuleVo result = service.create("tenant-1", "user-1", "turn-1", request);

        ArgumentCaptor<WatchRule> inserted = ArgumentCaptor.forClass(WatchRule.class);
        verify(mapper).insertIdempotent(inserted.capture());
        assertEquals(9001L, result.getRuleId());
        assertEquals(List.of(3L, 8L), result.getTicketCategoryIds());
        assertEquals("tenant-1", inserted.getValue().getTenantId());
        assertEquals("user-1", inserted.getValue().getUserId());
        assertEquals("turn-1", inserted.getValue().getIdempotencyKey());
        assertEquals(WatchRuleState.ACTIVE.name(), result.getRuleStatus());
        assertEquals(1L, result.getVersion());
        assertNotNull(result.getNextCheckAt());
    }

    @Test
    void repeatedCreateReturnsTheIdempotentWinner() {
        WatchRule existing = rule(9001L, 1001L, 4L, WatchRuleState.ACTIVE);
        when(mapper.selectOne(any())).thenReturn(existing);
        AgentWatchRuleCreateRequest request = new AgentWatchRuleCreateRequest();
        request.setProgramId(1001L);

        AgentWatchRuleVo result = service.create("tenant-1", "user-1", "turn-1", request);

        assertEquals(9001L, result.getRuleId());
        assertEquals(4L, result.getVersion());
        verify(mapper, never()).insertIdempotent(any());
    }

    @Test
    void statusReplayIsIdempotentEvenWithThePreviousVersion() {
        WatchRule existing = rule(9001L, 1001L, 4L, WatchRuleState.PAUSED);
        when(mapper.selectOne(any())).thenReturn(existing);
        AgentWatchRuleStatusRequest request = new AgentWatchRuleStatusRequest();
        request.setRuleId(9001L);
        request.setProgramId(1001L);
        request.setExpectedVersion(3L);
        request.setTargetStatus(WatchRuleState.PAUSED);

        AgentWatchRuleVo result = service.setStatus("tenant-1", "user-1", request);

        assertEquals(WatchRuleState.PAUSED.name(), result.getRuleStatus());
        assertEquals(4L, result.getVersion());
        verify(mapper, never()).update(any(), any(Wrapper.class));
    }

    @Test
    void staleUpdateIsRejectedInsteadOfOverwriting() {
        WatchRule existing = rule(9001L, 1001L, 4L, WatchRuleState.ACTIVE);
        when(mapper.selectOne(any())).thenReturn(existing);
        AgentWatchRuleUpdateRequest request = new AgentWatchRuleUpdateRequest();
        request.setRuleId(9001L);
        request.setProgramId(1001L);
        request.setExpectedVersion(3L);
        request.setMinRemaining(2L);

        WatchRuleException error = assertThrows(
                WatchRuleException.class,
                () -> service.update("tenant-1", "user-1", request));

        assertEquals(409, error.getCode());
        verify(mapper, never()).update(any(), any(Wrapper.class));
    }

    private WatchRule rule(Long id, Long programId, Long version, WatchRuleState state) {
        Date now = new Date();
        WatchRule rule = new WatchRule();
        rule.setId(id);
        rule.setTenantId("tenant-1");
        rule.setUserId("user-1");
        rule.setProgramId(programId);
        rule.setName("测试监控");
        rule.setMinRemaining(1L);
        rule.setCheckIntervalSeconds(300);
        rule.setNotificationChannel(WatchNotificationChannel.IN_APP.name());
        rule.setRuleState(state.name());
        rule.setNextCheckTime(now);
        rule.setCreateTime(now);
        rule.setEditTime(now);
        rule.setVersion(version);
        return rule;
    }
}
