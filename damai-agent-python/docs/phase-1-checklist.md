# 阶段 1：只读运行时内核

开始日期：2026-09-15

## 目标

在不改变现有 Chat API 和三个 Java 只读 Tool 的前提下，把最小闭环升级为职责清晰、默认拒绝、有预算且可审计的运行时内核。

## 第一批：运行契约与 Tool 策略

- [x] 增加 `AgentRunSpec`、`AgentRunResult`、`TicketTurnContext`、`AgentEvent`
- [x] 拆分 `TicketAgentLoop` 与纯执行层 `ToolCallingRunner`
- [x] 保留 `AgentRunner` 兼容入口，现有 API 无需迁移
- [x] Agent Event 增加事件 ID、单 Turn 递增序号、Trace、发生时间
- [x] Provider Response 增加结束原因、模型路由和 Token Usage
- [x] OpenAPI 生成 Tool 风险、Scope、超时、并发和单轮调用上限元数据
- [x] Tool 参数安全类型转换、默认值填充和 JSON Schema 校验
- [x] Tool Scope、风险上限、全局/单工具调用次数限制
- [x] Provider 拒绝或内容过滤时禁止执行夹带的 Tool Call
- [x] JSON/SSE 外部错误使用稳定错误码，不暴露原始异常文本

## 第二批：生成模型与真实流式

- [x] 从 OpenAPI 生成 Python 请求/响应模型，并校验 Java Tool Result
- [x] Provider 支持文本 Delta、Tool Call Delta、Usage 和标准结束事件
- [x] SSE 输出真实模型文本流，并增加流空闲超时

## 后续批次

- [x] 并发执行标记为安全的只读 Tool，独占 Tool 保持串行
- [ ] 增加 Hook 链：Trace、Policy、Audit、Usage
- [ ] 增加上下文预算、Tool Result 限长和协议配对治理
- [ ] 为三个真实 Java Tool 执行阶段 1 E2E 与故障注入验收

## 当前自动验收

- [x] 错误 Tool 参数不会到达执行器
- [x] 缺少 Scope 或超出风险上限时默认拒绝
- [x] 单 Tool 调用超过上限时返回稳定错误码
- [x] 同一 Session 并发 Turn 严格串行
- [x] 每个事件具有连续 `eventSeq` 和一致 `traceId`
- [x] 多轮 Provider Usage 正确累计到 `AgentRunResult`
- [x] 分片 Tool 参数可安全拼装，畸形 JSON 不进入执行器
- [x] Java 成功响应缺少契约字段或 `requestId` 不匹配时默认拒绝
- [x] SSE 连续无事件达到阈值后以稳定 `STREAM_IDLE_TIMEOUT` 终止
- [x] 相邻只读调用受限并发；独占、串行和非只读调用形成执行屏障
- [x] 并发时调用次数预算、超时/异常隔离、模型消息配对和事件序号保持正确
- [x] `AgentRunSpec` 无法伪造注册 Tool 的并发/独占策略
- [x] 阶段 0 的 3 个 Java Tool 行为保持兼容
- [x] Python 3.11：45 项测试通过，分支覆盖率 85.69%（门槛 70%）
- [x] OpenAPI、生成文件漂移和 v1 向后兼容检查通过
- [x] Java Maven Reactor 30 模块通过，Agent 契约/Trace 测试 6 项通过

## 第二批自动验收记录

- 日期：2026-09-16
- 生成契约：3 个 Tool 的请求/响应 Pydantic 模型与 ToolSpec 绑定成功
- Provider：文本 Delta、Tool Call Delta、Usage、结束原因均可聚合与透传
- Java 响应防线：缺失 Envelope 字段和 Tool Call ID 串单均返回 `TOOL_RESULT_INVALID`
- SSE：真实文本 Delta 保持连续事件序号，空闲超时不暴露内部异常
- 质量门禁：Ruff、Mypy strict、38 项 Pytest、84.98% 分支覆盖率全部通过

## 第三批：受控只读并发

- 日期：2026-09-16
- 默认最多同时运行 4 个相邻的授权只读 Tool，可通过 `DAMAI_AGENT_MAX_CONCURRENT_READ_TOOLS` 设置为 1～12
- 模型定义与运行策略以注册表为准；风险高于 `READ_ONLY` 或标记独占的工具不能并发
- Tool Result、事件与上下文按模型调用顺序提交；单个调用失败或超时不会丢弃同批结果
- Java 客户端仍为同步网络线程；超时后的物理取消及跨进程独占需后续阶段补强，不能把当前执行屏障视为交易级独占
- 质量门禁：Ruff、Mypy strict、OpenAPI 漂移/兼容性、45 项 Pytest 和 85.69% 分支覆盖率通过

## 第一批真实回归记录

- 日期：2026-09-15
- Java / Agent 健康状态：`UP` / `UP`
- 实际 Tool Call：`search_programs`
- Turn：`turn-31d18079-ac2e-453f-9062-5f3573e2377e`
- Trace：`50f80045dca449de82d98d15f9f52eaf`，Java 日志关联成功
- SSE：`turn.started → model.completed → tool.started → tool.completed → model.completed → turn.completed`
- SSE 序号：`1,2,3,4,5,6`，全程共享 Trace `fd311165f9b945038e7decf1737000e5`

## 阶段退出标准

现有三个 Tool 必须在认证链路下稳定流式工作；错误参数不得到达 Java；同 Session 串行；每个 Turn/Tool 都有 Trace 与 Usage；核心运行时通过单元、契约、集成和故障测试。
