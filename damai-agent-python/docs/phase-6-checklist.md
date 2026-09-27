# 阶段 6：购买意向与受信确认检查清单

阶段目标：允许 Agent 帮助用户形成购买意向，但价格、库存、确认授权和订单状态始终由 Java 掌握；没有 BFF 签发的有效一次性 Confirmation Grant，任何路径都不能提交订单。

## 第一批：意向、报价快照与确认边界

- [x] OpenAPI v1.2 新增 `prepare_purchase_intent`、`get_purchase_intent`、`cancel_purchase_intent`
- [x] Tool 请求禁止金额、身份、确认凭据和订单字段；Python 继续拒绝 `ORDER_WRITE` 风险上限
- [x] Java 重新读取实时票档，以人民币分生成短时有效报价和绑定归属/会话/版本的 SHA-256 摘要
- [x] PurchaseIntent 按 `program_id` 分片，创建使用 Turn ID 幂等，取消使用乐观版本
- [x] BFF 确认端点与 Agent Tool 分离，并使用独立 HMAC 密钥、时间窗和一次性 nonce
- [x] 确认前再次验证所有者、Session、版本、报价有效期、实时价格和库存
- [x] 意向确认与服务器侧 Confirmation Grant 在同一分片事务提交
- [x] Grant 不返回模型、不进入 Tool 参数或 Python Checkpoint
- [x] Java/Python 双侧默认关闭；Python 启用时强制 durable runtime 和持久化 Tool 审计
- [x] CI 覆盖金额换算、余票、幂等参数漂移、乐观取消、价格变化、签名绑定和时间窗

### BFF 确认证明

确认请求由 BFF 直接调用：

```text
POST /internal/agent/v1/confirmations/purchase-intents/issue
```

`X-Agent-Confirmation-Signature` 为以下 UTF-8 文本的 HMAC-SHA256 小写十六进制值，各字段以换行分隔：

```text
tenantId
userId
sessionKey
intentId
programId
expectedVersion
quoteHash
confirmedAt（解析后规范化为 Instant.toString()）
confirmationNonce
```

确认密钥来自 `AGENT_CONFIRMATION_HMAC_KEY`，不得与 `AGENT_DELEGATION_HMAC_KEY` 相同。BFF 必须先把节目、票档、数量、单价、总价和报价失效时间展示给用户，再签发证明；聊天文本中的“确认”不参与签名。

### 部署顺序

1. 在四个节目分片备份并执行 `sql/migrations/20260927_agent_purchase_intent.sql`。
2. 部署包含 PurchaseIntent/ConfirmationGrant 分片规则的 Java 版本，保持 `AGENT_PURCHASE_INTENTS_ENABLED=false`。
3. 在 Secret Manager 生成独立的 `AGENT_CONFIRMATION_HMAC_KEY`，Java BFF 与 Program Service 使用同一版本；Python 不应获得该密钥。
4. Python 使用 durable runtime、PostgreSQL/Redis 和 `DAMAI_AGENT_PERSIST_TOOL_AUDIT=true`，然后设置 `DAMAI_AGENT_PURCHASE_INTENTS_ENABLED=true`。
5. Java 设置 `AGENT_PURCHASE_INTENTS_ENABLED=true`；BFF 只在已登录用户进入明确确认 UI 后签发 `purchase:intent:*` 委托 Scope 或确认签名。
6. 验证重复准备、跨租户查询、参数漂移、过期报价、实时涨价、库存不足、重复 nonce、并发确认和取消冲突。

## 第二批：一次性订单提交（待开发）

- [ ] 新增默认不可见的 `submit_confirmed_order`，风险定级为 `ORDER_WRITE`
- [ ] Java 在同一业务幂等边界原子消费未过期 Grant，并调用现有订单状态机
- [ ] 意向 ID 成为订单幂等键；重复请求返回同一订单事实，不重复扣库存
- [ ] 超时或网络中断后先按意向 ID 查询订单事实，不盲目重试写操作
- [ ] 订单请求只接受 Java 管理的购票人引用，不允许模型传递身份证、手机号或支付凭据
- [ ] 提交结果、库存回滚、订单取消和 Grant 状态具备一致性审计

## 第三批：恢复、观测与外部验收（待开发）

- [ ] 覆盖进程在 Grant 消费前后、订单创建前后崩溃的恢复矩阵
- [ ] 建立确认拒绝、Grant 重放、订单幂等、未知结果和补偿指标/告警
- [ ] 在测试专用 MySQL、Redis、Kafka 和订单服务执行并发与故障注入
- [ ] 证明无确认提交数为 0、重复订单数为 0、跨租户访问数为 0
- [ ] 完成安全评审、交易 Runbook、密钥轮换和生产灰度审批

第一批完成只代表购买意向和确认授权边界已经建立，不代表 Agent 已能下单。`ORDER_WRITE` 仍被 Python 委托模型和 Java Tool 契约拒绝，生产环境不得把 `CONFIRMED` 意向描述为已创建订单。
