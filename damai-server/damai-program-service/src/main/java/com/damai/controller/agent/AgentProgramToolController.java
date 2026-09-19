package com.damai.controller.agent;

import cn.hutool.core.bean.BeanUtil;
import com.damai.controller.agent.dto.AgentProgramRecommendationRequest;
import com.damai.controller.agent.dto.AgentProgramRequest;
import com.damai.controller.agent.dto.AgentProgramSearchRequest;
import com.damai.controller.agent.dto.AgentRecommendationPreference;
import com.damai.controller.agent.vo.AgentProgramRecommendationPageVo;
import com.damai.controller.agent.vo.AgentProgramRecommendationVo;
import com.damai.controller.agent.vo.AgentToolResponse;
import com.damai.dto.ProgramGetDto;
import com.damai.dto.TicketCategoryListByProgramDto;
import com.damai.exception.DaMaiFrameException;
import com.damai.page.PageVo;
import com.damai.service.ProgramService;
import com.damai.service.TicketCategoryService;
import com.damai.vo.ProgramListVo;
import com.damai.vo.ProgramVo;
import com.damai.vo.TicketCategoryDetailVo;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.validation.BindException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 面向 Python Agent 的只读工具网关。
 *
 * 该控制器做契约适配，并为推荐执行有界的硬约束、实时库存核验和确定性排序。
 */
@Slf4j
@RestController
@RequestMapping("/internal/agent/v1/tools")
@Tag(name = "agent-program-tools", description = "Python Agent 使用的只读节目工具")
public class AgentProgramToolController {

    private static final String REQUEST_ID_HEADER = "X-Agent-Tool-Call-Id";

    @Autowired
    private ProgramService programService;

    @Autowired
    private TicketCategoryService ticketCategoryService;

    @Operation(summary = "搜索节目")
    @PostMapping("/programs/search")
    public AgentToolResponse<PageVo<ProgramListVo>> searchPrograms(
            @Parameter(description = "Agent 工具调用 id")
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId,
            @Valid @RequestBody AgentProgramSearchRequest request) {
        String normalizedRequestId = normalizeRequestId(requestId);
        PageVo<ProgramListVo> programs = programService.search(request.toProgramSearchDto());
        return AgentToolResponse.ok(normalizedRequestId, applyHardConstraints(programs, request));
    }

    @Operation(summary = "推荐有实时余票的节目")
    @PostMapping("/programs/recommendations")
    public AgentToolResponse<AgentProgramRecommendationPageVo> recommendPrograms(
            @Parameter(description = "Agent 工具调用 id")
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId,
            @Valid @RequestBody AgentProgramRecommendationRequest request) {
        PageVo<ProgramListVo> page = applyHardConstraints(
                programService.search(request.toRecommendationSearchDto()), request);
        List<ProgramListVo> programs = page == null || page.getList() == null
                ? List.of()
                : page.getList();
        List<Long> programIds = programs.stream()
                .filter(Objects::nonNull)
                .map(ProgramListVo::getId)
                .filter(Objects::nonNull)
                .distinct()
                .limit(10)
                .collect(Collectors.toList());
        Map<Long, List<TicketCategoryDetailVo>> inventory =
                ticketCategoryService.selectListByPrograms(programIds);
        AgentProgramRecommendationPageVo recommendations =
                buildRecommendations(programs, inventory, request);
        return AgentToolResponse.ok(normalizeRequestId(requestId), recommendations);
    }

    @Operation(summary = "查询节目详情")
    @PostMapping("/programs/detail")
    public AgentToolResponse<ProgramVo> getProgramDetail(
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId,
            @Valid @RequestBody AgentProgramRequest request) {
        ProgramGetDto dto = new ProgramGetDto();
        dto.setId(request.getProgramId());
        return AgentToolResponse.ok(normalizeRequestId(requestId), programService.detailV2(dto));
    }

    @Operation(summary = "查询节目票档与实时余量")
    @PostMapping("/programs/ticket-categories")
    public AgentToolResponse<List<TicketCategoryDetailVo>> listTicketCategories(
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId,
            @Valid @RequestBody AgentProgramRequest request) {
        TicketCategoryListByProgramDto dto = new TicketCategoryListByProgramDto();
        dto.setProgramId(request.getProgramId());
        return AgentToolResponse.ok(
                normalizeRequestId(requestId),
                ticketCategoryService.selectListByProgram(dto));
    }

    @ExceptionHandler(DaMaiFrameException.class)
    public AgentToolResponse<Void> handleBusinessException(
            DaMaiFrameException exception,
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId) {
        return AgentToolResponse.error(
                normalizeRequestId(requestId),
                Optional.ofNullable(exception.getCode()).orElse(-100),
                exception.getMessage(),
                false);
    }

    @ExceptionHandler({MethodArgumentNotValidException.class, BindException.class})
    public AgentToolResponse<Void> handleValidationException(
            Exception exception,
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId) {
        return AgentToolResponse.error(
                normalizeRequestId(requestId),
                400,
                "Agent 工具参数不合法",
                false);
    }

    @ExceptionHandler(Throwable.class)
    public AgentToolResponse<Void> handleUnexpectedException(
            Throwable exception,
            @RequestHeader(value = REQUEST_ID_HEADER, required = false) String requestId) {
        log.error("Agent tool execution failed, requestId={}", requestId, exception);
        return AgentToolResponse.error(
                normalizeRequestId(requestId),
                -100,
                "Agent 工具暂时不可用",
                true);
    }

    private String normalizeRequestId(String requestId) {
        return Optional.ofNullable(requestId)
                .filter(value -> !value.isBlank())
                .orElseGet(() -> UUID.randomUUID().toString());
    }

    static PageVo<ProgramListVo> applyHardConstraints(
            PageVo<ProgramListVo> page,
            AgentProgramSearchRequest request) {
        if (page == null || page.getList() == null) {
            return page;
        }
        List<ProgramListVo> original = page.getList();
        List<ProgramListVo> filtered = new ArrayList<>();
        for (ProgramListVo program : original) {
            if (matchesHardConstraints(program, request)) {
                filtered.add(program);
            }
        }
        page.setList(filtered);
        if (filtered.size() != original.size()) {
            // The upstream total includes candidates removed by the Agent-only budget guard.
            // Returning the bounded page count is conservative and never overstates eligibility.
            page.setTotalSize(filtered.size());
        }
        return page;
    }

    private static boolean matchesHardConstraints(
            ProgramListVo program,
            AgentProgramSearchRequest request) {
        if (program == null) {
            return false;
        }
        if (request.getAreaId() != null && !Objects.equals(request.getAreaId(), program.getAreaId())) {
            return false;
        }
        if (request.getParentProgramCategoryId() != null
                && !Objects.equals(
                        request.getParentProgramCategoryId(), program.getParentProgramCategoryId())) {
            return false;
        }
        if (request.getProgramCategoryId() != null
                && !Objects.equals(request.getProgramCategoryId(), program.getProgramCategoryId())) {
            return false;
        }
        if (request.getMaxPrice() != null
                && (program.getMinPrice() == null
                        || program.getMinPrice().compareTo(request.getMaxPrice()) > 0)) {
            return false;
        }
        if (Integer.valueOf(5).equals(request.getTimeType())) {
            Date showTime = program.getShowTime();
            if (showTime == null
                    || (request.getStartDateTime() != null
                            && showTime.before(request.getStartDateTime()))
                    || (request.getEndDateTime() != null
                            && showTime.after(request.getEndDateTime()))) {
                return false;
            }
        }
        return true;
    }

    static AgentProgramRecommendationPageVo buildRecommendations(
            List<ProgramListVo> programs,
            Map<Long, List<TicketCategoryDetailVo>> inventoryByProgram,
            AgentProgramRecommendationRequest request) {
        List<ProgramListVo> scanned = programs == null
                ? List.of()
                : programs.stream().limit(10).collect(Collectors.toList());
        List<AgentProgramRecommendationVo> eligible = new ArrayList<>();
        for (ProgramListVo program : scanned) {
            if (program == null || program.getId() == null) {
                continue;
            }
            List<TicketCategoryDetailVo> available = inventoryByProgram
                    .getOrDefault(program.getId(), List.of())
                    .stream()
                    .filter(Objects::nonNull)
                    .filter(ticket -> ticket.getPrice() != null)
                    .filter(ticket -> ticket.getRemainNumber() != null
                            && ticket.getRemainNumber() > 0)
                    .filter(ticket -> request.getMaxPrice() == null
                            || ticket.getPrice().compareTo(request.getMaxPrice()) <= 0)
                    .collect(Collectors.toList());
            if (available.isEmpty()) {
                continue;
            }
            AgentProgramRecommendationVo recommendation = new AgentProgramRecommendationVo();
            BeanUtil.copyProperties(program, recommendation);
            recommendation.setAvailableTicketCategoryCount(available.size());
            recommendation.setLowestAvailablePrice(available.stream()
                    .map(TicketCategoryDetailVo::getPrice)
                    .min(Comparator.naturalOrder())
                    .orElseThrow());
            recommendation.setTotalRemaining(totalRemaining(available));
            recommendation.setReasonCodes(reasonCodes(request));
            eligible.add(recommendation);
        }

        AgentRecommendationPreference preference = Optional.ofNullable(request.getPreference())
                .orElse(AgentRecommendationPreference.RELEVANCE);
        Comparator<AgentProgramRecommendationVo> comparator = recommendationComparator(preference);
        if (comparator != null) {
            eligible.sort(comparator);
        }
        int eligibleCount = eligible.size();
        int limit = Math.min(Optional.ofNullable(request.getCandidateLimit()).orElse(3), eligibleCount);
        List<AgentProgramRecommendationVo> selected = new ArrayList<>(eligible.subList(0, limit));
        for (int index = 0; index < selected.size(); index++) {
            selected.get(index).setRank(index + 1);
        }
        return new AgentProgramRecommendationPageVo(
                scanned.size(), eligibleCount, preference, selected);
    }

    private static long totalRemaining(List<TicketCategoryDetailVo> available) {
        long total = 0;
        for (TicketCategoryDetailVo ticket : available) {
            long remaining = ticket.getRemainNumber();
            if (Long.MAX_VALUE - total < remaining) {
                return Long.MAX_VALUE;
            }
            total += remaining;
        }
        return total;
    }

    private static List<String> reasonCodes(AgentProgramRecommendationRequest request) {
        List<String> reasons = new ArrayList<>();
        reasons.add("LIVE_INVENTORY_CONFIRMED");
        if (request.getMaxPrice() != null) {
            reasons.add("BUDGET_VERIFIED");
        }
        AgentRecommendationPreference preference = Optional.ofNullable(request.getPreference())
                .orElse(AgentRecommendationPreference.RELEVANCE);
        reasons.add("RANKED_BY_" + preference.name());
        return reasons;
    }

    private static Comparator<AgentProgramRecommendationVo> recommendationComparator(
            AgentRecommendationPreference preference) {
        Comparator<AgentProgramRecommendationVo> byId = Comparator.comparing(
                AgentProgramRecommendationVo::getId,
                Comparator.nullsLast(Comparator.naturalOrder()));
        return switch (preference) {
            case RELEVANCE -> null;
            case LOWEST_PRICE -> Comparator.comparing(
                            AgentProgramRecommendationVo::getLowestAvailablePrice)
                    .thenComparing(byId);
            case EARLIEST_SHOW -> Comparator.comparing(
                            AgentProgramRecommendationVo::getShowTime,
                            Comparator.nullsLast(Comparator.naturalOrder()))
                    .thenComparing(AgentProgramRecommendationVo::getLowestAvailablePrice)
                    .thenComparing(byId);
            case MOST_AVAILABLE -> Comparator.comparing(
                            AgentProgramRecommendationVo::getTotalRemaining,
                            Comparator.reverseOrder())
                    .thenComparing(AgentProgramRecommendationVo::getLowestAvailablePrice)
                    .thenComparing(byId);
        };
    }
}
