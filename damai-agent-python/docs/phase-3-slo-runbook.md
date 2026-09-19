# 阶段 3：只读链路 SLO 与故障验收手册

以下为测试专用环境的验收门槛草案；投产前需由业务与运维确认窗口、流量模型和阈值。没有实测报告时不能把阶段 3 标为生产验收通过。

## 指标与门槛

`/metrics` 为每个 Agent 进程提供固定标签的 Prometheus 文本指标。`turn` 的 `rejected` 表示上下文、预算或额度被安全拒绝，不计入服务可用性分母；Provider 拒绝完成、缺失费用核算数据和执行异常计入 `error`。模型和工具错误率应单独监控。采集须通过内部密钥及受限网络完成。

| SLI | 计算方法 | 测试环境建议门槛 |
|---|---|---|
| 只读 Turn 可用性 | `success / (success + error)`，排除 `rejected` | 7 日滚动 ≥ 99% |
| Turn 延迟 | 全实例 Turn 直方图 p95 | 代表性负载下 ≤ 10 秒 |
| 工具链路 | `tool` 的错误计数和 Java 侧同 Trace 日志核对 | 无未知结果被自动重放 |
| 恢复正确性 | 进程强杀、Redis/PG 故障后的 Turn/Checkpoint 核对 | 100% 不重复执行未知工具 |

查询示例：

```promql
sum(rate(damai_agent_requests_total{kind="turn",outcome="success"}[5m]))
/
clamp_min(sum(rate(damai_agent_requests_total{kind="turn",outcome=~"success|error"}[5m])), 0.000001)
```

```promql
histogram_quantile(0.95, sum by (le) (rate(damai_agent_duration_seconds_bucket{kind="turn"}[5m])))
```

建议在持续 10 分钟的 Turn 可用性低于 99%、p95 超过 10 秒或 `/ready` 返回 503 时告警。低流量时同时核对请求数量，避免单个请求触发误判。`/metrics` 为实例级；生产聚合必须保留采集系统的 `instance`/`job` 标签，不向业务请求添加租户或用户标签。

## 隔离故障矩阵

| 注入点 | CI/模拟验证 | 测试专用真实环境还需观察 |
|---|---|---|
| Provider 429/503、流式中断 | 只在首事件前重试/切换；部分流不重放 | 费用、Trace、回退路径与 p95 |
| Java Gateway 503、传输故障 | 返回可重试结果，但 Agent 不自动重放已发 Tool | Java 日志与相同 Trace ID、无重复调用 |
| Redis 租约/租户额度故障 | CI 真实 Redis 测试验证幂等与失效拒绝 | 多副本竞争、云端超时和告警 |
| PostgreSQL Checkpoint/审计故障 | CI 真实 PostgreSQL 验证事务、强杀恢复、审计冲突 | 连接耗尽、备份恢复与保留期 |

CI 结果只证明其隔离测试条件。真实 Java Gateway、模型提供商、Collector 与云数据库的受控故障注入须使用专门环境、专用命名空间和可回滚流量；不得在业务生产命名空间直接进行。

## 处置与回滚

1. 先看 `/ready`、Turn/Model/Tool 错误率和 p95，再用 `traceId` 关联 Agent、Java Gateway 与 Collector；不要在日志或告警里贴 Prompt、密钥或 Tool 返回体。
2. 对 `running` Turn，确认旧 Redis 租约已失效后，使用原租户、Session、Turn、幂等键走 `/api/v2/turns/recover`；未知 Tool 结果只能标记未知，不得手工改库或直接重发。
3. 观测平台故障时可关闭 OTLP 导出；模型回退异常时把重试次数设为 0 并移除备用路由；审计落库故障时暂停新的持久化流量，不能通过关闭审计开关掩盖已有未知 Turn。
4. 灰度回滚前保存指标窗口、Trace 样本、Java 侧 Tool 调用记录和数据库迁移/备份状态。不要回退仍被新版本写入的表结构；采用 Expand/Migrate/Contract 迁移流程。

## 尚需填写的验收记录

- 环境与版本：待测试专用环境确认。
- 压测窗口、并发、成功率和 p95：待实测。
- Provider/Java/Redis/PostgreSQL 故障注入记录与恢复耗时：待实测。
- 供应商账单与估算费用差异：待对账。
- 业务/运维签字与灰度回滚演练：待完成。
