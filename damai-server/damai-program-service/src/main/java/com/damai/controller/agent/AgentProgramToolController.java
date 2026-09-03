package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentProgramRequest;
import com.damai.controller.agent.dto.AgentProgramSearchRequest;
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

import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 面向 Python Agent 的只读工具网关。
 *
 * 该控制器只做契约适配，不重新实现节目、搜索或库存业务规则。
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
        return AgentToolResponse.ok(
                normalizedRequestId,
                programService.search(request.toProgramSearchDto()));
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
}
