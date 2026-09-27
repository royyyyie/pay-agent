# ADR-013：购买意向、报价与确认授权分离

- 状态：Accepted
- 日期：2026-09-27
- 关联：ADR-001、ADR-005、ADR-006

## Context

聊天中的“确认购买”是非结构化文本，可能来自模型误解、Prompt Injection、历史消息重放或网络重试，不能作为订单授权。价格和库存也会变化，模型提供的金额不能成为交易依据。Python Agent 能够恢复和重试 Turn，但不应成为订单状态、报价或授权的事实来源。

## Decision

1. Java Program Service 保存 `PurchaseIntent` 和不可变报价快照，按 `program_id` 与实时票档数据同分片路由。
2. 模型只可调用 `prepare_purchase_intent`、`get_purchase_intent` 和 `cancel_purchase_intent`；三者不接受价格、租户、用户、Confirmation Grant 或订单参数。
3. Java 根据实时票档生成 `unitAmountFen`、`totalAmountFen`、有效期和 `quoteHash`。人民币金额统一使用分整数，禁止浮点金额进入后续交易边界。
4. 确认端点不进入 Agent Tool OpenAPI。Java BFF 在用户完成明确 UI 确认后，使用独立的 `AGENT_CONFIRMATION_HMAC_KEY` 对归属、会话、意向、版本、报价摘要、确认时间和一次性 nonce 签名。
5. Java 再次检查归属、会话、意向版本、报价有效期、实时价格和库存，然后在同一事务中把意向变为 `CONFIRMED` 并写入服务器侧一次性 `ConfirmationGrant`。
6. Grant 只保存在 Java 数据库，不返回 bearer token，不进入 Prompt、模型上下文、Tool 参数、普通日志或 Python Checkpoint。
7. Python 和 Java 双侧功能开关默认关闭。启用时 Python 必须使用 durable runtime 与持久化 Tool 审计；Java 必须配置不少于 32 字符且不同于委托密钥的确认密钥。
8. 本批不开放 `ORDER_WRITE`。后续提交订单必须原子消费未过期 Grant、调用 Java 订单状态机并以意向 ID 作为幂等事实；不能仅依赖 Python 的 Tool Call 去重。

## Consequences

- 模型可以帮助形成结构化购买意向，但不能自行产生确认权或决定交易金额。
- 同一 Turn 的重复准备返回同一意向；参数发生变化时拒绝复用幂等键。
- 报价过期、价格变化、库存不足、版本冲突、归属或会话不匹配都会失败关闭。
- 阶段 6 只有在 Grant 一次性消费、订单幂等、未知结果恢复与真实故障注入全部完成后才能退出。
