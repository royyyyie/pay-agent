# 阶段 6 交易 Runbook

本文用于测试环境验收、生产灰度和故障处置。所有 SQL 仅作只读核对；不得通过手工 `UPDATE` 绕过状态机、Grant 或 fencing token。

## 1. 安全前提

- Python 仅从 Java BFF 接收短时签名委托。下单请求必须包含 `order:submit`，且 `riskCeiling` 必须精确等于 `ORDER_WRITE`。
- `AGENT_CONFIRMATION_HMAC_KEY` 与 `AGENT_DELEGATION_HMAC_KEY` 分别生成、分别授权、至少 32 个随机字符，禁止写入仓库、镜像、日志或 Python 环境。
- BFF 只有在用户界面展示最终节目、票档、购票人、数量和金额并获得明确操作后，才可签发确认签名。
- Program、Order 和 User Service 只允许内网服务身份访问。`/order/fact` 返回最小事实，不返回姓名、证件、手机号、地址或支付信息。
- 开关默认关闭。外部验收和审批完成前不得在生产开启订单提交。

## 2. 配置

Python 必需配置：

```text
DAMAI_AGENT_RUNTIME_BACKEND=durable
DAMAI_AGENT_POSTGRES_DSN=...
DAMAI_AGENT_REDIS_URL=...
DAMAI_AGENT_DELEGATION_HMAC_KEY=...
DAMAI_AGENT_PERSIST_TOOL_AUDIT=true
DAMAI_AGENT_PURCHASE_INTENTS_ENABLED=true
DAMAI_AGENT_ORDER_SUBMISSION_ENABLED=true
```

Program Service 必需配置：

```text
AGENT_PURCHASE_INTENTS_ENABLED=true
AGENT_ORDER_SUBMISSION_ENABLED=true
AGENT_CONFIRMATION_HMAC_KEY=...
AGENT_DELEGATION_HMAC_KEY=...
AGENT_ORDER_FACT_API_KEY=...
# Order Service 可在轮换窗口接受逗号分隔的当前值和前一值：
AGENT_ORDER_FACT_API_KEYS=当前值,前一值
```

`AGENT_ORDER_FACT_API_KEY` 只分发给 Program Service 与 Order Service，用于保护最小订单事实接口；不得复用确认密钥、委托密钥或 Python Tool API Key。

恢复 worker 可调参数：

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `AGENT_ORDER_WORKER_ID` | hostname | 多副本租约 owner，实例间必须可区分 |
| `AGENT_ORDER_WORKER_DELAY_MS` | 1000 | 扫描间隔 |
| `AGENT_ORDER_LEASE_SECONDS` | 30 | 单次处理租约；必须大于正常查询 P99 |
| `AGENT_ORDER_RETRY_SECONDS` | 5 | 写前事实查询失败的初始退避 |
| `AGENT_ORDER_MAXIMUM_ATTEMPTS` | 6 | 写前查询最大尝试数 |
| `AGENT_ORDER_SCAN_PARTITIONS` | 4 | 按 `program_id` 扫描分区数 |
| `AGENT_ORDER_BATCH_SIZE` | 20 | 单周期最大认领数，上限 100 |

## 3. 部署与灰度顺序

1. 备份四个 Program 分片，确认已执行 `20260927_agent_purchase_intent.sql`，再执行 `sql/migrations/20260928_agent_order_submission.sql`。该迁移的 `ALTER TABLE` 只能执行一次。
2. 向 Program/Order Service 注入相同的独立 `AGENT_ORDER_FACT_API_KEY`，部署 Order Service 与 order-client；验证无密钥访问被拒绝，正确密钥对存在/不存在订单分别返回正确事实/`ORDER_NOT_EXIST`。
3. 部署 Program Service 与新的 ShardingSphere 规则，保持两个 Java 开关为 `false`；确认四张物理提交表均可路由。
4. 部署 Python，保持 `DAMAI_AGENT_ORDER_SUBMISSION_ENABLED=false`；验证 13 个 OpenAPI Tool 已加载，但模型不可见 `submit_confirmed_order`。
5. 开启购买意向，不开启订单提交。执行购票人安全引用、准备、重复准备、取消、确认、过期报价和 nonce 重放测试。
6. 在隔离测试租户中先开启 Java 订单提交，再开启 Python 订单提交。BFF 只给测试账号签发 `order:submit + ORDER_WRITE`。
7. 单副本跑通后扩为至少两个 Program Service 副本，执行第 7 节故障矩阵。
8. 生产按租户/账号白名单灰度；观察至少一个完整业务峰值窗口后再扩大。禁止用全量开关代替灰度授权。

旧 PurchaseIntent 的 `ticket_user_refs` 为空，不能提交订单；用户必须重新创建意向，不得后台补写身份引用。

## 4. 状态与恢复语义

| 中断点 | 持久事实 | 恢复动作 | 是否允许再次创建 |
|---|---|---|---|
| Grant 消费事务前 | `CONFIRMED + AVAILABLE` | 用户可重新提交 | 是，首次创建 |
| Grant/命令/意向事务中 | 全部提交或全部回滚 | 按最终数据库事实继续 | 仅 `PENDING` 首次处理允许 |
| 命令已提交、worker 未运行 | `PENDING` | worker 租约认领，先查订单 | 是，且仅在确认不存在后 |
| 写前事实查询失败 | `RETRY` | 有界指数退避后重新查询 | 查询成功前禁止 |
| 无订单事实后、创建调用前进程退出 | 过期 `PROCESSING` | 按未知结果查询；查不到转人工 | 否，保守处理 |
| 创建调用超时/连接中断 | `RECONCILE` | 有界只读查询；匹配则 `SUBMITTED`，达到上限仍不存在则人工 | 否 |
| 远端已创建、状态回写前退出 | 过期 `PROCESSING` | 查询匹配订单并自动对账 | 否 |
| 订单事实绑定不匹配 | `MANUAL_REVIEW` | 安全/交易联合调查 | 否 |
| 明确业务失败且已完成补偿 | `FAILED` | 用户重新创建意向 | 否 |

只有 `SUBMITTED` 才表示订单创建成功。`SUBMITTING` 是进行中，`SUBMISSION_FAILED` 是明确失败，`SUBMISSION_UNKNOWN` 必须人工核验。

## 5. 指标与告警

Micrometer 逻辑指标如下；Prometheus 会把点转换为下划线：

| 指标 | 标签 | 用途 |
|---|---|---|
| `damai.agent.purchase.confirmation.rejections` | `reason` | 确认签名、状态或参数拒绝 |
| `damai.agent.purchase.grant.replays` | `reason=idempotent|nonce` | 合法重复确认与 nonce 重放 |
| `damai.agent.purchase.submission.rejections` | `reason` | 下单边界拒绝 |
| `damai.agent.purchase.submission.outcomes` | `outcome` | `submitted/reconciled/retry/reconcile/failed/manual_review/stale` |
| `damai.agent.purchase.submission.backlog` | 无 | 到期未处理命令数 |

最低告警建议：

- `manual_review` 或 `reconcile` 增量大于 0：立即通知交易值班；禁止自动重放。
- `failed` 在 5 分钟内持续增长：检查库存状态机与补偿流水。
- `unexpected` 拒绝增量大于 0：检查服务异常与攻击流量。
- nonce 重放较历史基线上升 3 倍：检查 BFF 重试、前端重复提交或重放攻击。
- backlog 连续 5 分钟超过 `实例数 × batch-size × 5`：检查 Order Service、数据库租约和 worker 存活。
- `stale` 持续增长：检查 worker ID 唯一性、租约时长、数据库延迟和滚动发布节奏。

指标中禁止添加 tenant、user、session、intent、program 或 orderNumber 标签；这些高基数字段只进入受控 Trace/审计。

## 6. 人工复核

先按 `program_id` 计算实际分片，再执行只读查询：

```sql
SELECT intent_id, program_id, user_id, order_number, submission_state,
       attempt_count, last_error_code, create_time, edit_time
FROM d_agent_order_submission_N
WHERE submission_state IN ('RECONCILE', 'MANUAL_REVIEW')
ORDER BY edit_time;
```

复核必须同时收集：

1. Program 分片中的 PurchaseIntent、ConfirmationGrant 和 PurchaseOrderSubmission；
2. Order Service 的最小订单事实及订单创建时间；
3. Program V3 库存扣减/回滚流水、跨服务 Trace 和对应 Tool 审计；
4. BFF 确认事件元数据，但不得导出证件、完整签名或密钥。

如果订单事实匹配，使用受审计的修复任务调用与 `markSubmitted` 等价的领域操作；如果不存在，先确认库存无悬挂再关闭事件。仓库当前故意不提供“人工强制成功”HTTP 接口，避免绕过审批。

## 7. 外部验收矩阵

每个场景至少执行 20 次；并发场景至少 50 个并发请求。保存时间、版本、Trace ID、意向 ID、订单号哈希、期望/实际结果和复核人。

| 场景 | 注入方式 | 必须结果 |
|---|---|---|
| 无确认直接提交 | 跳过 BFF 确认 | 100% 拒绝，订单数与库存不变 |
| 跨租户/跨用户读取或提交 | 替换签名委托身份 | 100% 拒绝，无事实泄漏 |
| 同意向并发提交 | 50 并发相同请求 | 一个提交命令、一个订单号、Grant 只消费一次 |
| 创建前 Order 查询不可用 | 阻断 `/order/fact` | 只进入 `RETRY`，恢复后最多创建一次 |
| 创建响应丢失 | 远端提交后丢弃响应 | `RECONCILE` 后自动对账，创建调用总数不增加 |
| 创建调用中杀死 Program 实例 | 终止进程并等待租约过期 | 新副本只查询；匹配则对账，否则人工 |
| 状态回写前杀死实例 | 远端订单存在后终止 | 新副本对账为 `SUBMITTED` |
| 订单事实被篡改/错绑 | 测试桩返回错用户/节目/金额 | `MANUAL_REVIEW`，不得重写 |
| 明确库存不足 | 构造低库存 | `FAILED` 且库存/订单流水一致 |
| Kafka/Redis/Nacos 短暂故障 | 受控断连 | 不绕过 Java 业务状态机；恢复后事实一致 |

退出红线：无确认订单数 = 0，重复订单数 = 0，跨租户成功数 = 0，未知结果盲重放数 = 0，PII 泄漏数 = 0。任何一项非零都必须停止灰度。

## 8. 回滚与密钥轮换

紧急止写时先关闭 Python `DAMAI_AGENT_ORDER_SUBMISSION_ENABLED`，再关闭 Java `AGENT_ORDER_SUBMISSION_ENABLED`。这会同时停止新请求和恢复 worker；仍有 `PENDING/RETRY/RECONCILE/PROCESSING` 时必须保留一个已验证版本完成对账，不能长期搁置，也不能删除表或回退迁移。

普通版本回滚应先停止新授权，等待 backlog 清零，再滚动回退代码。数据库新增列和表保持向前兼容，不执行破坏性降级。

确认密钥当前为单值配置，轮换时先停止新确认、等待现有 Grant TTL 与最大时钟偏差窗口结束，再同步滚动 BFF 与 Program Service；不得在存在有效 Grant 时直接切换。委托密钥独立轮换，任何时候都不得与确认密钥相同。订单事实密钥支持双值窗口：先在 Order Service 的 `AGENT_ORDER_FACT_API_KEYS` 加入新旧两值，再把 Program Service 的 `AGENT_ORDER_FACT_API_KEY` 切换为新值，最后从 Order Service 删除旧值。轮换前后各执行一次无密钥拒绝、事实查询、签名成功、旧签名过期和 nonce 重放验收。

## 9. 验收记录

```text
环境：
Git commit / 镜像 digest：
数据库迁移版本：
Program / Order / User Service 版本：
开始与结束时间：
执行场景与次数：
无确认订单数：
重复订单数：
跨租户成功数：
未知结果盲重放数：
MANUAL_REVIEW 清单与结论：
指标/Trace/审计证据位置：
安全审批：
交易审批：
SRE 审批：
回滚演练结果：
```
