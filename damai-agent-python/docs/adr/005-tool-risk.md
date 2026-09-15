# ADR-005：Tool 风险分级与受信确认

- 状态：Accepted
- 日期：2026-09-06

## Context

模型输出具有概率性，不能直接等价为授权或交易指令。

## Decision

Tool 分为 `READ_ONLY`、`REVERSIBLE_WRITE`、`ORDER_WRITE` 和 `PROHIBITED`。高风险 Tool 默认不可见；订单类动作必须由 Java 校验绑定用户、意向版本、金额和有效期的一次性 Confirmation Grant。

## Consequences

聊天中的“确认”不是受信授权。所有写 Tool 必须具备幂等键、审计和 Java 二次鉴权。
