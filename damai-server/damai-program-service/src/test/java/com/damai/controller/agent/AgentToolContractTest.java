package com.damai.controller.agent;

import com.damai.controller.agent.dto.AgentProgramSearchRequest;
import com.damai.controller.agent.vo.AgentToolResponse;
import com.damai.page.PageVo;
import com.damai.vo.ProgramListVo;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AgentToolContractTest {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper().findAndRegisterModules();
    private static ValidatorFactory validatorFactory;
    private static Validator validator;

    @BeforeAll
    static void createValidator() {
        validatorFactory = Validation.buildDefaultValidatorFactory();
        validator = validatorFactory.getValidator();
    }

    @AfterAll
    static void closeValidator() {
        validatorFactory.close();
    }

    @Test
    void sharedSearchRequestFixtureBindsToJavaDto() throws IOException {
        AgentProgramSearchRequest request = OBJECT_MAPPER.readValue(
                fixture("search-request.json").toFile(), AgentProgramSearchRequest.class);
        Set<ConstraintViolation<AgentProgramSearchRequest>> violations = validator.validate(request);

        assertTrue(violations.isEmpty());
        assertEquals("周杰伦", request.getKeyword());
        assertEquals(310000L, request.getAreaId());
        assertEquals(3, request.getTimeType());
        assertEquals(5, request.getPageSize());
        assertEquals(new BigDecimal("800"), request.getMaxPrice());
    }

    @Test
    void sharedResponseFixturesMatchJavaEnvelope() throws IOException {
        JsonNode success = OBJECT_MAPPER.readTree(fixture("search-response.json").toFile());
        JsonNode error = OBJECT_MAPPER.readTree(fixture("tool-error-response.json").toFile());

        assertEnvelope(success, true);
        assertEnvelope(error, false);
        assertTrue(success.path("data").isObject());
        assertTrue(error.path("data").isNull());
    }

    @Test
    void javaResponseFactorySerializesRequiredContractFields() {
        JsonNode response = OBJECT_MAPPER.valueToTree(
                AgentToolResponse.ok("call_contract_test", OBJECT_MAPPER.createObjectNode()));

        assertEnvelope(response, true);
        assertEquals("call_contract_test", response.path("requestId").asText());
    }

    @Test
    void recommendationBudgetAndAreaAreHardFilters() {
        AgentProgramSearchRequest request = new AgentProgramSearchRequest();
        request.setAreaId(310000L);
        request.setMaxPrice(new BigDecimal("500"));

        ProgramListVo eligible = program(1L, 310000L, "380");
        ProgramListVo overBudget = program(2L, 310000L, "680");
        ProgramListVo wrongArea = program(3L, 110000L, "180");
        PageVo<ProgramListVo> page = new PageVo<>(1, 20, 3, List.of(
                eligible, overBudget, wrongArea));

        PageVo<ProgramListVo> filtered =
                AgentProgramToolController.applyHardConstraints(page, request);

        assertEquals(List.of(eligible), filtered.getList());
        assertEquals(1, filtered.getTotalSize());
    }

    private static ProgramListVo program(long id, long areaId, String minPrice) {
        ProgramListVo program = new ProgramListVo();
        program.setId(id);
        program.setAreaId(areaId);
        program.setMinPrice(new BigDecimal(minPrice));
        return program;
    }

    private static Path fixture(String name) {
        Path path = Path.of(
                        "..", "..", "contracts", "examples", "agent-tools-v1", name)
                .toAbsolutePath()
                .normalize();
        assertTrue(Files.isRegularFile(path), () -> "Shared contract fixture missing: " + path);
        return path;
    }

    private static void assertEnvelope(JsonNode response, boolean expectedSuccess) {
        assertTrue(response.path("requestId").isTextual());
        assertEquals(expectedSuccess, response.path("success").asBoolean());
        assertTrue(response.path("code").isIntegralNumber());
        assertTrue(response.path("message").isTextual());
        assertNotNull(response.get("data"));
        assertTrue(response.path("retryable").isBoolean());
        assertTrue(response.path("freshnessAt").isTextual());
        assertFalse(response.path("freshnessAt").asText().isBlank());
    }
}
