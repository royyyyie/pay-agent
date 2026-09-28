# ADR-014：订单提交使用持久化命令与查询优先恢复

- 状态：Accepted
- 日期：2026-09-28
- 关联：ADR-001、ADR-005、ADR-013

## Context

订单创建跨越 Program Service、库存状态机和 Order Service。HTTP 超时只能说明调用方没有收到结果，不能证明服务端没有创建订单。Python Turn Checkpoint 的幂等只能约束模型工具调用，不能替代 Java 交易幂等，也不能安全判断未知写入是否可以重试。

## Decision

1. `submit_confirmed_order` 是 `ORDER_WRITE` Tool，默认不暴露。它必须同时通过 Python 功能开关、持久化审计、签名委托中的 `order:submit` Scope 和精确 `ORDER_WRITE` 上限，以及 Java 双开关。
2. Program Service 在同一 `program_id` 分片事务内锁定 PurchaseIntent 与 ConfirmationGrant，创建唯一 PurchaseOrderSubmission、消费 Grant，并把意向转为 `SUBMITTING`。
3. PurchaseOrderSubmission 在第一次提交时生成稳定订单号；所有恢复和对账都使用该订单号，不重新生成业务标识。
4. Java 每次创建前调用 Order Service 的最小订单事实查询。事实匹配订单号、用户、节目和总价时直接标记成功；不匹配时转人工。
5. 事实查询失败发生在写入前，可以进入有界指数退避。订单创建调用一旦出现可重试异常，其结果被视为未知并进入 `RECONCILE`；后续 worker 只进行有界、只读的指数退避查询，不再创建。达到上限仍查不到事实时进入 `MANUAL_REVIEW`。
6. 进程在 `PROCESSING` 状态退出后，由数据库租约接管。接管者按未知写入处理，即使查询未发现订单也不能盲目重放。
7. Program Service 复用现有 V3 库存/订单状态机；Order Service 只新增由独立服务密钥保护、无 PII 的最小事实查询。Python 不访问业务数据库，也不保存 Grant、身份证件或支付信息。
8. `SUBMITTED` 是唯一可对用户声明“订单已创建”的状态。`SUBMITTING`、`RECONCILE` 和 `SUBMISSION_UNKNOWN` 都必须表述为处理中或需人工核验。

## Consequences

- 一次确认只能产生一个持久化提交命令和稳定订单号。
- 系统在网络分区下优先避免重复扣库存/重复订单，代价是部分不确定请求会进入人工复核。
- `MANUAL_REVIEW` 不允许直接修改为成功；运维必须先核对 Order Service 事实、库存流水和 Trace，再执行受审计处置。
- 自动化测试可以证明状态机与恢复策略，真实多服务事务、补偿和时序仍需测试专用环境故障注入。

## Supersedes

本 ADR 完成 ADR-013 第 8 条中预留的订单提交与未知结果恢复设计；不改变 ADR-013 的模型外确认边界。
