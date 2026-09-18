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
- [x] 增加 Hook 链：Trace、Policy、Audit、Usage
- [x] 增加字符级上下文预算、Tool Result 限长和协议配对治理
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
- [x] Hook 每轮独立创建；Usage 不跨 Turn 累计；Policy 在执行前拒绝未授权 Tool
- [x] 审计记录仅包含时间、Trace/Turn/调用标识、Tool、风险、结果码和耗时，不含参数及结果体
- [x] 未注册 Tool 名称与异常调用 ID 不进入审计原文；后置 Hook 故障不丢失 Tool Result
- [x] 旧对话按完整轮次裁剪；当前轮超预算时不调用模型且返回稳定错误码
- [x] Tool Result 超限时不向模型发送不完整业务字段；重复调用 ID 或孤立 Tool Result 默认拒绝
- [x] 阶段 0 的 3 个 Java Tool 行为保持兼容
- [x] Python 3.11：61 项测试通过，分支覆盖率 87.63%（门槛 70%）
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

## 第四批：运行时 Hook 链

- 日期：2026-09-16
- `PolicyHook` 在 Tool 执行前依据本轮授权列表默认拒绝；策略 Hook 异常时阻止执行并返回稳定错误码
- `TraceHook` 记录模型/Tool 完成元数据；`UsageHook` 每轮累计 Provider Token Usage；扩展 Hook 由工厂按 Turn 创建
- `AuditHook` 为每次 Tool 尝试生成带 UTC 时间戳的结构化记录；审计字段不包含 Prompt、Tool 参数、Tool 返回体或异常原文
- 默认 Sink 写本进程日志；持久化、完整性保护、保留期、访问控制和投递失败处理尚未实现，不能视为生产级不可抵赖审计
- 后置观测 Hook 或审计 Sink 故障不会吞掉对应 Tool Result；生产接入前仍需为自定义 Sink 设计可靠投递与告警
- 本地质量门禁：Ruff、Mypy strict、OpenAPI 漂移/兼容性、52 项 Pytest 和 87.09% 分支覆盖率通过

## 第五批：上下文与协议治理

- 日期：2026-09-16
- 模型请求的消息与 Tool Schema 按序列化字符数计入预算；超限先删除最旧的完整对话轮次，保留系统提示与当前轮
- 当前轮无法容纳时返回 `CONTEXT_BUDGET_EXCEEDED`，不发起下一次模型请求；这是字符保护而非精确 Token/费用预算
- 单个 Tool Result 超限时返回 `TOOL_RESULT_TOO_LARGE`；不截断票价、库存或规则等业务字段，以免模型引用不完整事实
- 模型 Tool Call ID 必须非空且批内唯一；历史中的 Assistant Tool Call 与 Tool Result 必须按 ID、名称和顺序完整配对，异常时返回 `CONTEXT_PROTOCOL_INVALID`
- 进程内会话按完整轮次淘汰旧消息，不再从轮次中间截断；多实例持久化与 Token 级预算仍待后续阶段
- 本地质量门禁：Ruff、Mypy strict、OpenAPI 漂移/兼容性、61 项 Pytest 和 87.63% 分支覆盖率通过
- 本次未执行真实 Java E2E：本机 `127.0.0.1:6086` 与 `127.0.0.1:9010` 均未启动；该退出标准仍待验收

## 第一批真实回归记录

- 日期：2026-09-15
- Java / Agent 健康状态：`UP` / `UP`
- 实际 Tool Call：`search_programs`
- Turn：`turn-31d18079-ac2e-453f-9062-5f3573e2377e`
- Trace：`50f80045dca449de82d98d15f9f52eaf`，Java 日志关联成功
- SSE：`turn.started → model.completed → tool.started → tool.completed → model.completed → turn.completed`
- SSE 序号：`1,2,3,4,5,6`，全程共享 Trace `fd311165f9b945038e7decf1737000e5`

## 云端中间件真实链路补充验收

- 日期：2026-09-18；通过现有云端 Nacos、Redis、Kafka、Elasticsearch 配置启动 Java Program Service，并启动 Python Agent；两侧健康状态均为 `UP`
- 三个 Java 只读 Tool：`search_programs`、`get_program_detail`、`list_ticket_categories` 的真实调用均返回业务码 0，每种 Tool 各重复 3 次通过；Agent 对话/SSE 主链和事件连续性通过
- 验收 Trace：`ecb6d3f79e3f419684aeb807cf733d94`；同一 Turn 中事件序号连续、Trace 一致；Java 错误密钥请求返回 401
- 首次 `get_program_detail` 曾发生约 5 秒超时，重试通过；原因未查明，不能据此认定稳定性退出标准已满足
- 尚未完成网络/进程故障注入、跨实例竞争与断线恢复验收；上方“真实 Java Tool E2E 与故障注入”总项仍保持未完成
- 因 Java 服务默认监听所有网卡且本地密钥强度不足，验收后已停止本次启动的 Java 与 Python 进程；云端中间件未被修改

## 阶段退出标准

现有三个 Tool 必须在认证链路下稳定流式工作；错误参数不得到达 Java；同 Session 串行；每个 Turn/Tool 都有 Trace 与 Usage；核心运行时通过单元、契约、集成和故障测试。
