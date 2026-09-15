# ADR-009：HTTP+SSE 的初始通信方案

- 状态：Accepted
- 日期：2026-09-06

## Context

当前系统已有 HTTP 和 SSE；过早引入消息总线会增加运维与一致性成本。

## Decision

只读企业版继续使用内部 HTTP + 可恢复 SSE。事件使用单调 `eventSeq` 持久化并支持 `Last-Event-ID`。只有吞吐、跨区域或可靠异步消费需求被量化后才引入 Kafka。

## Consequences

SSE 断线不等同 Turn 取消；客户端可通过 Turn 查询和事件序号恢复。
