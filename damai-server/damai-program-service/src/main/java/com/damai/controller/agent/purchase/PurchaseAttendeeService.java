package com.damai.controller.agent.purchase;

import com.damai.client.UserClient;
import com.damai.common.ApiResponse;
import com.damai.controller.agent.vo.AgentPurchaseAttendeeRefVo;
import com.damai.dto.TicketUserListDto;
import com.damai.enums.BaseCode;
import com.damai.vo.TicketUserVo;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/** Returns only safe references owned by the trusted user context. */
@Service
public class PurchaseAttendeeService {

    private final UserClient userClient;

    public PurchaseAttendeeService(UserClient userClient) {
        this.userClient = userClient;
    }

    public List<AgentPurchaseAttendeeRefVo> list(String userId) {
        long numericUserId = numericUserId(userId);
        return ownedAttendees(numericUserId).stream()
                .limit(20)
                .map(item -> new AgentPurchaseAttendeeRefVo(
                        item.getId(), "购票人-" + Math.floorMod(item.getId(), 10000)))
                .toList();
    }

    public void requireOwned(String userId, List<Long> ticketUserIds) {
        long numericUserId = numericUserId(userId);
        Set<Long> ownedIds = ownedAttendees(numericUserId).stream()
                .map(TicketUserVo::getId)
                .collect(Collectors.toSet());
        if (ticketUserIds == null || !ownedIds.containsAll(ticketUserIds)) {
            throw new PurchaseIntentException(422, "购票人引用不属于当前账户");
        }
    }

    private List<TicketUserVo> ownedAttendees(long numericUserId) {
        TicketUserListDto request = new TicketUserListDto();
        request.setUserId(numericUserId);
        ApiResponse<List<TicketUserVo>> response = userClient.list(request);
        if (response == null
                || !Objects.equals(response.getCode(), BaseCode.SUCCESS.getCode())
                || response.getData() == null) {
            throw new PurchaseIntentException(503, "购票人引用服务暂时不可用");
        }
        return response.getData().stream()
                .filter(item -> item != null
                        && item.getId() != null
                        && Objects.equals(item.getUserId(), numericUserId))
                .toList();
    }

    private long numericUserId(String userId) {
        try {
            long value = Long.parseLong(userId);
            if (value <= 0) {
                throw new NumberFormatException("non-positive");
            }
            return value;
        } catch (NumberFormatException exception) {
            throw new PurchaseIntentException(422, "用户标识不能映射到购票账户");
        }
    }
}
