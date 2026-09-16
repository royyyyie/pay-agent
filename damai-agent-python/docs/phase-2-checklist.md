# 阶段 2：持久化与恢复

开始日期：2026-09-16

## 目标与前置条件

目标是让 Session/Turn 可持久化、可幂等、可安全恢复，并在多副本下保持同一租户与 Session 严格串行。阶段 1 的三个真实 Java Tool 端到端及故障注入验收仍未完成，不得据此宣布只读生产准入。

## 第一批：Checkpoint 安全契约

- [x] 定义包含租户、Session、Turn、迭代、版本、Prompt/Toolset/Policy 版本与模型路由的 `AgentCheckpoint`
- [x] 工具结果按调用 ID 记录，拒绝重复 ID、未知结果及不合法的工具调用批次
- [x] 恢复时按原调用顺序配对结果；状态不明的调用补 `TOOL_EXECUTION_UNKNOWN`，不推定成功，也不自动重放
- [x] 定义 Checkpoint Repository 接口与单进程测试实现，使用版本比较防止过期写入和过期清理
- [x] 同一租户与 Session 在测试实现中只能有一个活动 Checkpoint，读写时复制可变内容

第一批仅建立领域契约和单进程行为，不会自动写入生产运行中的 Checkpoint。内存实现无法在进程崩溃后保留数据，不得用于多副本或生产恢复；检查点可能含 Tool 参数和结果，持久化时必须加密、鉴权并设置保留期。

本地验收：Python 3.11 下 67 项测试通过、分支覆盖率 87.72%；Ruff、Mypy strict、OpenAPI/生成文件/兼容性检查通过。本机没有 PostgreSQL、Redis 或 Docker，尚无真实持久化与中断恢复测试证据。

## 后续批次

- [ ] PostgreSQL Session/Message/Turn/Checkpoint Repository、迁移和原子提交
- [ ] Redis 带 owner token 的 Session 租约锁、续租、Pending Queue 与取消标记
- [ ] Runner 在工具执行前、每个结果完成后写 Checkpoint；恢复协议并保证任何未知写操作不盲目重放
- [ ] Turn 幂等键与重复请求返回同一结果；冲突请求默认拒绝
- [ ] 断线 SSE 事件持久化与续传、委托身份和请求级 Scope
- [ ] PostgreSQL/Redis 故障、进程中断、多副本竞争和真实 Java Tool 注入验收

## 阶段退出标准

进程在模型/Tool 安全点中断后可恢复合法协议，Turn 不丢失；多副本中同 Session 不并发；未知写操作不会被重复执行；断线 SSE 可续传。必须有真实 PostgreSQL、Redis 和 Java 链路故障测试证据，单进程测试不能替代。
