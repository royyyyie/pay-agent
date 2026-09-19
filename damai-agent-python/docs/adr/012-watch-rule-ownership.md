# ADR-012：监控规则由 Java 持久化和调度

- 状态：Accepted
- 日期：2026-09-19
- 关联：ADR-001、ADR-005、ADR-006

## Context

票务监控是长期业务状态，不属于一次 Agent Turn。Python Agent 进程会扩缩容、重启和恢复 Checkpoint；若由它常驻轮询，会出现重复任务、租约丢失、通知风暴以及动态库存被写入对话状态的问题。监控规则还必须服从用户归属、实时库存、频率限制和通知去重，这些均属于 Java 业务域。

## Decision

1. Python 只负责理解用户意图并调用版本化 WatchRule Tool，不运行长期轮询。
2. Java Program Service 保存规则、执行调度、读取实时票档并产生通知事件。规则按不可变 `program_id` 分片。
3. 规则写 Tool 定级为 `REVERSIBLE_WRITE`，同时要求 HMAC 签名委托中的 `watch:write` Scope、持久化 Tool 审计和显式功能开关。订单类风险仍不开放。
4. `tenantId`、`userId` 只从签名委托透传的受信 Header 获得；请求 Schema 禁止身份字段。Python 验证委托后原样向 Java Tool Gateway 转发正文与签名，Java 再次验证 HMAC、有效期、Scope、风险上限及请求绑定，所有查询和写入均执行所有者条件。
5. 创建以 `(tenant, user, program, turnId)` 幂等；修改和状态切换使用 `expectedVersion` 乐观锁。规则 ID 单独不足以完成写操作，必须同时携带 `programId` 分片键。
6. 规则状态首批只有 `ACTIVE` 和 `PAUSED`，通知渠道首批只有 `IN_APP`。扩大状态机或新增外部通知渠道必须补充安全、退订和送达语义。
7. 调度执行采用数据库认领和租约，通知采用 Transactional Outbox 与业务去重键；这些执行面未交付前，不得把阶段 5 标记为完成。

## Consequences

- Agent 重启不会成为规则丢失或重复执行的来源，Python Checkpoint 也不承担任务调度一致性。
- 控制面依赖 MySQL 分片迁移，启用前必须先建表并确认 Java/Python 双侧功能开关与审计。
- 列表查询可能跨分片，必须分页且每页不超过 20；调度查询使用各分片的 `rule_state + next_check_time` 索引。
- 当前批次只交付安全控制面。没有调度 Worker、实时条件求值、通知 Outbox 和去重验收时，规则不会产生通知。
