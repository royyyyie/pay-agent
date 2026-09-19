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

- [ ] 分片扫描到期规则，使用数据库租约/跳过锁定完成多副本安全认领
- [ ] 批量读取实时票档，严格执行票档白名单、价格上限和最小余量
- [ ] 每次执行记录规则版本、检查时间、结果摘要和下一次检查时间
- [ ] 超时、Java 重启和租约过期可重新认领；同一版本不会并发检查
- [ ] 暂停规则不会被新认领；执行中的旧版本结果不得触发通知
- [ ] 调度积压、检查延迟、成功率和外部依赖错误进入固定标签指标

## 第三批：通知 Outbox 与去重

- [ ] 规则状态更新与通知 Outbox 在同一事务提交
- [ ] 去重键至少包含规则 ID、规则版本、条件指纹和受控时间窗
- [ ] Kafka 发布支持重试，消费者以事件 ID 幂等；任一侧重启不重复送达
- [ ] 通知包含节目、匹配票档、实时价格/余量和 `freshnessAt`，不包含敏感身份
- [ ] 支持冷却窗口、退订/暂停和失败死信处置
- [ ] 完成规则重启不丢失、并发认领、通知重复投递和 Kafka 故障注入

## 当前退出判断

第一批完成只代表“监控规则可以安全持久化和管理”，不代表监控闭环已经完成。阶段 5 的退出标准仍需第二、三批全部通过，并在测试专用环境证明：Java/Agent 重启不丢规则，多副本不重复执行，同一触发不会重复通知。
