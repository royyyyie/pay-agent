# ADR-007：Provider 路由与重试语义

- 状态：Accepted
- 日期：2026-09-06

## Context

模型服务可能限流、超时或返回部分流，通用重试可能重复用户可见内容或 Tool 动作。

## Decision

Model Gateway 统一处理模型选择、超时、`Retry-After`、指数退避、fallback、熔断和 Usage。只有明确暂时性且尚未产生不可安全重放输出的请求才能自动重试。

## Consequences

Provider Adapter 不承载产品策略；每次 Turn 固化实际模型路由和生成参数用于审计。
