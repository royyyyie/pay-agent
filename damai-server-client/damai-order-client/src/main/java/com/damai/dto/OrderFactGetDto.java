package com.damai.dto;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Min;
import lombok.Data;

/** Minimal internal order-fact lookup used by ambiguous-result recovery. */
@Data
public class OrderFactGetDto {

    @NotNull
    @Min(1)
    private Long orderNumber;
}
