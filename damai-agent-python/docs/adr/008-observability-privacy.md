# ADR-008：Trace、审计与 PII

- 状态：Accepted
- 日期：2026-09-06

## Context

Agent 调用跨越用户入口、模型和多个业务服务，需要关联排障，同时购票信息可能包含敏感数据。

## Decision

采用 W3C Trace Context 和 OpenTelemetry。业务 ID 作为受控属性；Tool 调用另存审计记录。密钥、证件、支付信息、完整 Prompt 和购票人详情不得进入普通日志或 Trace。

## Consequences

所有日志和事件经过结构化脱敏；审计存储使用独立权限和保留策略。
