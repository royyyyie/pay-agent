# 阶段 5：监控闭环检查清单

阶段目标：Agent 可以在受信身份下创建、修改、查询、暂停和恢复票务监控；Java 可靠调度实时检查并通过去重通知形成闭环，服务重启不丢规则、不重复通知。

## 第一批：安全控制面

- [x] OpenAPI v1.1 增加 `create_watch_rule`、`update_watch_rule`、`set_watch_rule_status`、`list_watch_rules`
- [x] 写操作定级为 `REVERSIBLE_WRITE`，每轮最多一次且作为串行独占屏障
- [x] 签名委托仅新增 `REVERSIBLE_WRITE` 上限；继续拒绝 `ORDER_WRITE`
- [x] `tenantId`、`userId` 仅通过受信 Header 传递，Java 再次验证原始 HMAC 委托、有效期、Scope、风险上限与请求绑定
- [x] 创建使用 Turn ID 作为幂等键；修改和状态切换使用乐观版本
- [x] Java 按 `program_id` 分片持久化规则，单用户最多启用 20 条
- [x] Python 启用监控规则时强制 durable runtime 与 PostgreSQL Tool 审计
- [x] Java/Python 双侧默认关闭功能开关，不会因部署新代码自动开放写权限
- [x] OpenAPI 生成模型、兼容性测试、Java 状态机单测和身份边界测试进入 CI

启用顺序：

1. 备份并执行 `sql/migrations/20260919_agent_watch_rule.sql`。
2. 将同一分片规则加入实际使用的 ShardingSphere Profile；仓库的 local/pro Profile 已包含逻辑表配置。
3. Java 设置 `AGENT_WATCH_RULES_ENABLED=true`，并通过 Secret Manager 设置与 Python 验证端一致的 `AGENT_DELEGATION_HMAC_KEY`。
4. Python 设置 durable runtime、`DAMAI_AGENT_PERSIST_TOOL_AUDIT=true` 和 `DAMAI_AGENT_WATCH_RULES_ENABLED=true`。
5. Java BFF 只给已登录用户签发 `watch:read` / `watch:write` Scope，并按请求把 `riskCeiling` 提升到 `REVERSIBLE_WRITE`。
6. 先验证列表，再验证创建、重复创建、并发修改冲突、暂停和恢复；审计记录不得包含完整规则参数或用户输入。

## 第二批：可靠调度与实时条件求值

- [x] 分片扫描到期规则，使用数据库租约和 fencing token 完成多副本安全认领
- [x] 批量读取实时票档，严格执行票档白名单、价格上限和最小余量
- [x] 每次执行记录规则版本、检查时间、结果摘要和下一次检查时间
- [x] 超时、Java 重启和租约过期可重新认领；同一版本不会并发检查
- [x] 暂停规则不会被新认领；执行中的旧版本结果不得进入后续通知入口
- [x] 调度积压、检查延迟、成功率和外部依赖错误进入固定标签指标

实现约束：

- 候选查询按 `program_id` 模数分区；实际认领以携带 `program_id`、规则版本和租约过期条件的单分片原子更新为准，因此多个 Java 副本可以安全扫描相同候选。
- 每次认领生成新的随机 fencing token。完成执行时必须再次匹配规则版本、启用状态、租约 owner、token 和过期时间；修改、暂停会递增版本并清除旧租约。
- 成功、无匹配、票档依赖错误和陈旧执行都写入 `d_agent_watch_execution`。成功完成规则状态和执行流水位于同一节目分片、同一事务。
- 指标只使用 `outcome`、`dependency` 两类固定标签，不包含租户、用户、规则或节目等高基数标识。

部署和验收顺序：

1. 在四个节目分片执行 `sql/migrations/20260920_agent_watch_scheduler.sql`，然后部署包含 `d_agent_watch_execution` 分片规则的新版本。
2. 保持 `AGENT_WATCH_SCHEDULER_ENABLED=false` 启动并检查迁移；确认执行表可按 `program_id` 正确路由。
3. 先在单副本设置 `AGENT_WATCH_RULES_ENABLED=true`、`AGENT_WATCH_SCHEDULER_ENABLED=true`，观察一个检查周期；再扩到至少两个副本。
4. 验证 Actuator/Prometheus 指标 `damai.agent.watch.scheduler.backlog`、`check.delay`、`executions` 和 `dependency.errors`。
5. 人工制造慢检查并等待租约过期，确认新副本可以重新认领；并发修改或暂停同一规则，确认旧 token 只产生 `STALE` 执行，不能进入通知入口。

## 第三批：通知 Outbox 与去重

- [x] 规则状态更新、执行流水与通知 Outbox 在同一节目分片事务提交
- [x] 去重键包含规则 ID、规则版本、条件指纹和受控时间窗
- [x] Kafka 发布支持 DB 租约、指数退避和幂等 Producer；消费者以事件 ID 幂等
- [x] 通知包含节目、匹配票档、实时价格/余量和 `freshnessAt`，Kafka 内容不包含租户或用户身份
- [x] 支持冷却窗口、退订/暂停抑制、Publisher DEAD 状态和 Consumer DLT
- [x] CI 覆盖 fencing、冷却去重、重复消费、暂停抑制和 Kafka 故障注入

可靠性语义：

- Scheduler 在确认规则版本和租约 token 后，同事务更新 `last_checked_time` / `last_triggered_time`、写执行流水并写唯一 Outbox；任一写入失败都会整体回滚。
- Outbox Publisher 只有收到 Kafka Broker ACK 后才标记 `PUBLISHED`。ACK 后数据库更新失败会造成重复投递，但事件 ID 不变，`d_agent_watch_notification` 的唯一键会吸收重复。
- Kafka 事件不携带 `tenantId`、`userId` 或用户输入的规则名称。Consumer 使用 `ruleId + programId` 本地查询当前归属；规则已暂停或版本已变化时只写 `SUPPRESSED` 回执，不生成可展示通知。
- Producer 连续失败达到上限后 Outbox 进入 `DEAD`；Consumer 连续失败由专用 Listener Factory 投递至 `<notification-topic>.dlt`，不会阻塞正常事件。

部署和外部验收顺序：

1. 备份后执行 `sql/migrations/20260921_agent_watch_outbox.sql`，部署包含 Outbox/通知分片规则的应用，保持 `AGENT_WATCH_NOTIFICATION_ENABLED=false`。
2. 预创建 `${AGENT_WATCH_NOTIFICATION_TOPIC}` 与同分区数的 `${AGENT_WATCH_NOTIFICATION_TOPIC}.dlt`；生产至少 4 个分区，副本数和 `min.insync.replicas` 按 Kafka 集群容灾标准设置。
3. 确认 Producer 使用 `acks=all`、`enable.idempotence=true`，Consumer 使用独立版本化 Group ID；再开启 `AGENT_WATCH_NOTIFICATION_ENABLED=true`。
4. 触发一条匹配规则，核对执行流水、Outbox `PUBLISHED`、站内通知 `DELIVERED` 三段证据及 `freshnessAt`；Kafka Payload 不得出现租户或用户标识。
5. 重复投递相同事件，通知表只能有一条；发布后立即暂停或修改规则，旧版本事件必须为 `SUPPRESSED`。
6. 阻断 Kafka 后确认 Outbox 指数退避并最终 `DEAD`；注入不可解析消息后确认进入 `.dlt`；恢复后通过受控运维流程重放，不直接修改去重键。

## 当前退出判断

阶段 5 的代码与自动化测试已经覆盖三批能力，但退出仍需在测试专用 MySQL/Kafka 环境执行上述外部验收，证明：Java/Agent 重启不丢规则，多副本不重复执行，同一事件不重复生成站内通知，Kafka 故障可进入并恢复自 Outbox/DLT。生产开关在外部验收前保持关闭。
