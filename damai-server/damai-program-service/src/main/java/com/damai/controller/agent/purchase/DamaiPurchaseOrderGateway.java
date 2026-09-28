package com.damai.controller.agent.purchase;

import com.damai.client.OrderClient;
import com.damai.common.ApiResponse;
import com.damai.dto.OrderFactGetDto;
import com.damai.dto.ProgramOrderCreateDto;
import com.damai.enums.BaseCode;
import com.damai.enums.ProgramOrderVersion;
import com.damai.exception.DaMaiFrameException;
import com.damai.service.strategy.ProgramOrderContext;
import com.damai.vo.OrderFactVo;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Objects;
import java.util.Optional;
import java.util.Set;

/** Uses the production V3 inventory/order path with a stable trusted order number. */
@Component
public class DamaiPurchaseOrderGateway implements PurchaseOrderGateway {

    private static final Set<Integer> TERMINAL_CODES = Set.of(
            BaseCode.PROGRAM_NOT_EXIST.getCode(),
            BaseCode.SEAT_NOT_EXIST.getCode(),
            BaseCode.SEAT_LOCK.getCode(),
            BaseCode.SEAT_SOLD.getCode(),
            BaseCode.SEAT_OCCUPY.getCode(),
            BaseCode.TICKET_CATEGORY_NOT_EXIST.getCode(),
            BaseCode.TICKET_COUNT_NOT_EXIST.getCode(),
            BaseCode.TICKET_COUNT_ERROR.getCode(),
            BaseCode.TICKET_REMAIN_NUMBER_NOT_SUFFICIENT.getCode(),
            BaseCode.TICKET_USER_EMPTY.getCode());

    private final OrderClient orderClient;
    private final ProgramOrderContext programOrderContext;
    private final String orderFactKey;

    public DamaiPurchaseOrderGateway(
            OrderClient orderClient,
            ProgramOrderContext programOrderContext,
            @Value("${AGENT_ORDER_FACT_API_KEY:}") String orderFactKey) {
        this.orderClient = orderClient;
        this.programOrderContext = programOrderContext;
        this.orderFactKey = orderFactKey;
    }

    @Override
    public Optional<PurchaseOrderFact> find(Long orderNumber) {
        OrderFactGetDto request = new OrderFactGetDto();
        request.setOrderNumber(orderNumber);
        ApiResponse<OrderFactVo> response = orderClient.fact(orderFactKey, request);
        if (response != null && Objects.equals(response.getCode(), BaseCode.SUCCESS.getCode())) {
            OrderFactVo fact = response.getData();
            if (fact == null) {
                throw new PurchaseOrderGatewayException("ORDER_FACT_EMPTY", true, null);
            }
            return Optional.of(new PurchaseOrderFact(
                    fact.getOrderNumber(),
                    fact.getProgramId(),
                    fact.getUserId(),
                    fact.getOrderPrice(),
                    fact.getOrderStatus(),
                    fact.getCreateOrderTime()));
        }
        if (response != null && Objects.equals(response.getCode(), BaseCode.ORDER_NOT_EXIST.getCode())) {
            return Optional.empty();
        }
        throw new PurchaseOrderGatewayException("ORDER_FACT_UNAVAILABLE", true, null);
    }

    @Override
    public Long create(PurchaseOrderSubmission submission) {
        ProgramOrderCreateDto request = new ProgramOrderCreateDto();
        request.setProgramId(submission.getProgramId());
        request.setUserId(numericUserId(submission.getUserId()));
        request.setTicketUserIdList(PurchaseTicketUserRefs.parse(submission.getTicketUserRefs()));
        request.setTicketCategoryId(submission.getTicketCategoryId());
        request.setTicketCount(submission.getQuantity());
        request.setTrustedOrderNumber(submission.getOrderNumber());
        try {
            String result = programOrderContext
                    .get(ProgramOrderVersion.V3_VERSION.getVersion())
                    .createOrder(request);
            long orderNumber = Long.parseLong(result);
            if (orderNumber != submission.getOrderNumber()) {
                throw new PurchaseOrderGatewayException("ORDER_NUMBER_MISMATCH", false, null);
            }
            return orderNumber;
        } catch (PurchaseOrderGatewayException exception) {
            throw exception;
        } catch (DaMaiFrameException exception) {
            Integer code = exception.getCode();
            boolean retryable = code == null || !TERMINAL_CODES.contains(code);
            throw new PurchaseOrderGatewayException(
                    code == null ? "ORDER_PIPELINE_FAILURE" : "ORDER_PIPELINE_" + code,
                    retryable,
                    exception);
        } catch (RuntimeException exception) {
            throw new PurchaseOrderGatewayException(
                    "ORDER_PIPELINE_UNEXPECTED", true, exception);
        }
    }

    private long numericUserId(String userId) {
        try {
            return Long.parseLong(userId);
        } catch (NumberFormatException exception) {
            throw new PurchaseOrderGatewayException("USER_ID_INVALID", false, exception);
        }
    }
}
