# 阶段 6：受信确认、订单提交与安全恢复检查清单

阶段目标：Agent 可以在用户完成模型外明确确认后提交订单；价格、库存、购票人、授权、订单号和最终状态始终由 Java 掌握。任何未知写入结果都只能查询对账，不能由模型或恢复 worker 盲目重放。

## 第一批：意向、报价快照与确认边界

- [x] OpenAPI 新增 `prepare_purchase_intent`、`get_purchase_intent`、`cancel_purchase_intent`
- [x] Tool 请求禁止金额、身份、确认凭据和订单字段
- [x] Java 重新读取实时票档，以人民币分生成短时有效报价和绑定归属/会话/版本的 SHA-256 摘要
- [x] PurchaseIntent 按 `program_id` 分片，创建使用 Turn ID 幂等，取消使用乐观版本
- [x] BFF 确认端点与 Agent Tool 分离，并使用独立 HMAC 密钥、时间窗和一次性 nonce
- [x] 确认前再次验证所有者、Session、版本、报价有效期、实时价格和库存
- [x] 意向确认与服务器侧 Confirmation Grant 在同一分片事务提交
- [x] Grant 不返回模型、不进入 Tool 参数或 Python Checkpoint
- [x] Java/Python 双侧默认关闭；Python 启用时强制 durable runtime 和持久化 Tool 审计
- [x] CI 覆盖金额换算、余票、幂等参数漂移、乐观取消、价格变化、签名绑定和时间窗

### BFF 确认证明

确认请求由 BFF 直接调用 `POST /internal/agent/v1/confirmations/purchase-intents/issue`。`X-Agent-Confirmation-Signature` 是以下 UTF-8 文本的 HMAC-SHA256 小写十六进制值，各字段以换行分隔：

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

确认密钥来自 `AGENT_CONFIRMATION_HMAC_KEY`，不得与 `AGENT_DELEGATION_HMAC_KEY` 相同。BFF 必须先展示节目、票档、购票人、数量、单价、总价和报价失效时间；聊天文本中的“确认”不参与授权。

## 第二批：一次性订单提交

- [x] OpenAPI v1.3 新增 `list_purchase_attendees` 和默认不可见的 `submit_confirmed_order`
- [x] 提交 Tool 定级 `ORDER_WRITE`，要求精确 `order:submit` Scope、每 Turn 最多一次并作为独占屏障
- [x] Python 只在 durable runtime、持久化审计、购买意向与订单开关同时开启时暴露提交 Tool
- [x] 购票人由 Java 用户服务返回安全引用；模型不能传递姓名、身份证、手机号、地址或支付凭据
- [x] 意向报价摘要绑定有序购票人引用，数量必须一致且引用不得重复
- [x] Java 锁定意向与 Grant，在同一分片事务中创建持久化提交命令、消费 Grant 并转移意向状态
- [x] 每个意向只产生一个稳定订单号；意向 ID 和订单号都有数据库唯一约束
- [x] 订单创建复用 Program Service V3 库存与订单状态机，不在 Python 重写业务规则
- [x] Order Service 提供最小化、无 PII 的订单事实查询，用于写前检查与中断对账
- [x] 最小订单事实接口使用独立服务密钥保护；密钥缺失或过弱时订单功能启动失败
- [x] 重复提交返回同一提交事实；不会再次消费 Grant 或生成新订单号
- [x] 提交成功后只有 Java 的 `SUBMITTED + orderNumber` 可以被 Agent 描述为已下单

## 第三批：恢复、观测与安全封板

- [x] 数据库租约和 fencing token 支持多副本认领，陈旧 worker 不能提交状态
- [x] 每次创建前先查询订单事实；已有匹配订单直接对账，不再次调用创建
- [x] 创建调用超时或网络错误进入 `RECONCILE`，后续只允许有界查询；达到上限仍查不到才转 `MANUAL_REVIEW`
- [x] 崩溃后遗留的 `PROCESSING` 同样按未知写入处理，禁止盲目重放
- [x] 只有创建前的事实查询失败可安全指数退避；达到上限转人工
- [x] 订单号、用户、节目或总价任一对账不匹配立即转人工
- [x] 自动化恢复矩阵覆盖 Grant 原子消费、重复提交、既有订单对账、未知结果、绑定冲突和安全重试
- [x] 指标覆盖确认拒绝、Grant 重放、提交拒绝、提交成功、对账成功、安全重试、未知结果、失败和积压
- [x] CI 同时编译 Program/Order Service，并运行全部 `AgentTool*` Java 测试和 Python 契约/运行时测试
- [x] 安全边界、部署顺序、告警、人工复核、密钥轮换和回滚步骤已形成 Runbook

## 外部生产准入（必须在测试专用环境执行）

- [ ] 在测试 MySQL、Redis、Nacos、Kafka、Program Service、Order Service 和 User Service 执行完整链路
- [ ] 注入 Program/Order Service 超时、响应丢失、Java 进程退出和多副本租约接管
- [ ] 并发提交同一意向，证明无确认提交数为 0、重复订单数为 0、跨租户访问数为 0
- [ ] 对 `RECONCILE`、`MANUAL_REVIEW`、库存补偿和订单事实完成数据库及 Trace 三方核对
- [ ] 安全负责人、交易负责人和 SRE 审批生产灰度，并记录可追溯证据

仓库工程的阶段 6 已完成；生产准入不能由 Mock/单元测试替代。外部验收完成前，`DAMAI_AGENT_ORDER_SUBMISSION_ENABLED` 与 `AGENT_ORDER_SUBMISSION_ENABLED` 必须保持 `false`。部署与证据模板见[阶段 6 交易 Runbook](phase-6-operations.md)，架构决策见 [ADR-014](adr/014-durable-order-submission.md)。
