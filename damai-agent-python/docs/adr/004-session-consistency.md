# ADR-004：Session、Turn 与 Checkpoint 一致性

- 状态：Accepted
- 日期：2026-09-06

## Context

内存 Session 无法支持多实例、滚动升级和执行中崩溃恢复。

## Decision

PostgreSQL 保存 Session、Message、Turn 和 Checkpoint；Redis 保存带租约的 Session 锁、Pending Queue、取消标记和事件扇出。同一租户与 Session 严格串行，Turn 使用幂等键。

## Consequences

阶段 2 引入 Repository 接口和迁移。未完成写 Tool 恢复时必须先查询 Java 事实，禁止盲目重放。
