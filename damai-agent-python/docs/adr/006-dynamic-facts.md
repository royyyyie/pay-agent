# ADR-006：动态票务事实禁止由 RAG 或 Memory 回答

- 状态：Accepted
- 日期：2026-09-06

## Context

库存、价格、开售和订单状态变化频繁，历史或向量检索结果可能已经过期。

## Decision

动态票务事实必须来自实时 Java Tool，并携带真实数据采集时间。RAG 只保存规则、场馆、交通、实名制和 FAQ 等相对稳定知识。

## Consequences

Output Guard 和 Agent Eval 必须检查事实来源；无法获得实时数据时明确回答无法确认。
