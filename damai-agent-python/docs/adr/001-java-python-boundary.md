# ADR-001：Java/Python 职责与数据归属

- 状态：Accepted
- 日期：2026-09-06

## Context

LLM 编排需要快速迭代，订单、库存和支付需要确定性事务与严格状态机。

## Decision

Python 负责模型、上下文、工具编排、会话记忆、流式事件和评测。Java 是用户、演出、库存、价格、购买意向、订单、支付、风控和审计事实的唯一权威来源。Python 不直连业务数据库。

## Consequences

所有业务能力通过 Java Tool Gateway 暴露；Python Checkpoint 只能恢复消息协议，不能改变 Java 业务事实。
