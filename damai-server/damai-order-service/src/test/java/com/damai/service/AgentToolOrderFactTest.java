package com.damai.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.damai.common.ApiResponse;
import com.damai.controller.OrderController;
import com.damai.dto.OrderFactGetDto;
import com.damai.entity.Order;
import com.damai.enums.BaseCode;
import com.damai.exception.DaMaiFrameException;
import com.damai.mapper.OrderMapper;
import com.damai.vo.OrderFactVo;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Date;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AgentToolOrderFactTest {

    @Test
    void returnsOnlyStableFieldsNeededForReconciliation() {
        OrderMapper mapper = mock(OrderMapper.class);
        OrderService service = new OrderService();
        ReflectionTestUtils.setField(service, "orderMapper", mapper);
        Order order = new Order();
        order.setOrderNumber(88001L);
        order.setProgramId(1001L);
        order.setUserId(42L);
        order.setOrderPrice(new BigDecimal("1160.00"));
        order.setOrderStatus(1);
        order.setCreateOrderTime(Date.from(Instant.parse("2026-09-28T08:00:00Z")));
        when(mapper.selectOne(any(LambdaQueryWrapper.class))).thenReturn(order);
        OrderFactGetDto request = new OrderFactGetDto();
        request.setOrderNumber(88001L);

        OrderFactVo fact = service.fact(request);

        assertEquals(88001L, fact.getOrderNumber());
        assertEquals(1001L, fact.getProgramId());
        assertEquals(42L, fact.getUserId());
        assertEquals(new BigDecimal("1160.00"), fact.getOrderPrice());
        assertEquals(1, fact.getOrderStatus());
    }

    @Test
    void missingOrderIsAnExplicitBusinessFact() {
        OrderMapper mapper = mock(OrderMapper.class);
        OrderService service = new OrderService();
        ReflectionTestUtils.setField(service, "orderMapper", mapper);
        when(mapper.selectOne(any(LambdaQueryWrapper.class))).thenReturn(null);
        OrderFactGetDto request = new OrderFactGetDto();
        request.setOrderNumber(88001L);

        assertThrows(DaMaiFrameException.class, () -> service.fact(request));
    }

    @Test
    void controllerFailsClosedWithoutTheDedicatedServiceKey() {
        OrderService service = mock(OrderService.class);
        OrderController controller = new OrderController();
        ReflectionTestUtils.setField(controller, "orderService", service);
        ReflectionTestUtils.setField(
                controller, "agentOrderFactApiKeys", "k".repeat(32));
        OrderFactGetDto request = new OrderFactGetDto();
        request.setOrderNumber(88001L);

        ApiResponse<OrderFactVo> rejected = controller.fact("wrong", request);

        assertEquals(BaseCode.ONLY_SIGNATURE_ACCESS_IS_ALLOWED.getCode(), rejected.getCode());
        verify(service, never()).fact(request);
    }

    @Test
    void controllerAcceptsTheActiveKeyDuringDualKeyRotation() {
        OrderService service = mock(OrderService.class);
        OrderController controller = new OrderController();
        ReflectionTestUtils.setField(controller, "orderService", service);
        ReflectionTestUtils.setField(
                controller,
                "agentOrderFactApiKeys",
                "o".repeat(32) + "," + "n".repeat(32));
        OrderFactGetDto request = new OrderFactGetDto();
        request.setOrderNumber(88001L);
        OrderFactVo expected = new OrderFactVo();
        expected.setOrderNumber(88001L);
        when(service.fact(request)).thenReturn(expected);

        ApiResponse<OrderFactVo> accepted = controller.fact("n".repeat(32), request);

        assertEquals(BaseCode.SUCCESS.getCode(), accepted.getCode());
        assertEquals(88001L, accepted.getData().getOrderNumber());
        verify(service).fact(request);
    }
}
