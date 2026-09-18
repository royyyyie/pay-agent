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

- [x] PostgreSQL 活动 Checkpoint Repository、迁移和同一租户/Session 原子版本比较写入；GitHub PostgreSQL 集成测试通过
- [x] PostgreSQL Session/Message/Turn Repository、迁移和 Turn 完成/消息追加/Checkpoint 清理的单事务提交；新增真实数据库测试在 GitHub CI 通过
- [x] Redis 带 owner token 的 Session 租约锁、条件续租与条件释放；云端 Redis 验证过期旧 owner 不会释放新租约
- [ ] Redis Pending Queue；显式持久化入口已支持取消标记并在模型/Tool 安全点检查，运行中外部调用仍不能被强制取消
- [x] 显式持久化 Runner 在工具执行前、每个结果完成后写 Checkpoint；中断 Turn 可由持有原请求的调用方显式终结恢复，未知状态的调用不自动重放
- [x] Repository 层 Turn 幂等键与重复请求返回同一结果；显式持久化入口已接入，默认 Chat API 仍未切换
- [ ] 断线 SSE 事件持久化与续传、委托身份和请求级 Scope
- [ ] PostgreSQL/Redis 故障、进程中断、多副本竞争和真实 Java Tool 注入验收

## 第二批：PostgreSQL 活动 Checkpoint

- 迁移脚本：`migrations/001_agent_active_checkpoint.sql`；按 `(tenant_id, session_key)` 唯一约束限制活动 Turn；数据库事务内锁行校验并执行版本比较更新
- 存储格式由 `schema_version=1` 固定；读取时同时校验 JSON 结构、数据库行身份与版本，异常数据默认拒绝
- `PostgresCheckpointRepository` 使用独立短事务，不在模型或 Tool 网络调用期间持有数据库连接；数据库连接池与运行时接入尚未实现
- `postgres` 为可选依赖；仅安装依赖不会把现有运行时切换到 PostgreSQL。生产接入前必须完成受控数据库账号、传输/静态加密、备份、保留期/清理与连接池容量设计
- CI 的 PostgreSQL 只用于测试；它不替代云端服务。当前批次仍没有 Session/Message/Turn 持久化，也没有崩溃恢复的端到端证据
- 本地检查：69 项通过，4 项真实 PostgreSQL 集成测试因没有测试 DSN 被跳过；Ruff、Mypy strict、OpenAPI/生成文件/兼容性检查通过。GitHub [质量与 Java 契约检查](https://github.com/royyyyie/pay-agent/actions/runs/35343252226)均通过

## 第三批：Redis Session 租约基础

- `RedisSessionLeaseStore` 以租户和 Session 的哈希值构造隔离键；随机 owner token 与 TTL 一起原子抢占，续租和释放通过 Redis 脚本核对 owner 后执行
- Redis 网络/认证错误向调用方抛出，不退化为“已获锁”；TTL 到期后，旧 owner 不能续租或释放新 owner 的键
- 2026-09-18：使用现有云端 Redis 的随机测试命名空间和短期键验证，5 项测试通过，涵盖并发抢占、续租、过期接管、旧 owner 拒绝和租户隔离；未读取或修改业务键
- 云端往返耗时使最初 3 秒竞争测试产生先后获锁的误判；测试改用 30 秒 TTL 后通过，过期接管使用独立测试验证
- CI 已配置独立 Redis 服务，GitHub 运行待确认。该租约尚未接入 Agent Loop；单 Redis 租约不能代替数据库 fencing token，租约失效期间运行中的旧进程仍可能继续执行，故不能据此宣称多副本严格串行
- Agent 工作流同时响应 `codex/phase-*` 分支推送，可在尚未创建 PR 时运行 PostgreSQL/Redis 验收
- 全套本地测试加载现有云端 Redis 配置后为 74 项通过、4 项 PostgreSQL 测试因缺少测试 DSN 跳过，覆盖率 85.54%；Ruff、Mypy strict、OpenAPI 与契约兼容检查通过

## 第四批：PostgreSQL Session/Turn 原子提交基础

- `migrations/002_agent_session_turn.sql` 建立租户隔离的 Session、Turn 与 Message 表；每个 Session 同时只能有一个活动 Turn，完成后保留有序消息
- `PostgresTurnRepository` 以幂等键和请求指纹区分重复请求与冲突请求：进行中的重复请求不取得写入代次，已完成的重复请求返回已存结果
- Session 行锁串行化开始/接管/完成，递增 `fence_version`；恢复持有者接管同一 Turn 后，旧代次不能完成 Turn
- Turn 完成、消息追加、活动 Checkpoint 清理在同一 PostgreSQL 事务中；Checkpoint 版本错误会整体回滚，不产生半完成消息
- 最近消息按完整 Turn 选取，不从 Tool 调用与结果之间截断；读取时校验存储格式版本
- 该层还未接入 Agent Loop/API，Redis 租约持有状态尚未由数据库方法验证，Checkpoint 的独立更新也尚未携带 fencing 代次；因此仍不能宣布跨副本严格串行或安全自动恢复
- 新增的真实 PostgreSQL 并发、幂等、接管和原子回滚测试已在 GitHub [分支推送](https://github.com/royyyyie/pay-agent/actions/runs/35344002000)与 [PR](https://github.com/royyyyie/pay-agent/actions/runs/35344006196) 的 `quality` 检查通过；`java-contract` 同时通过。本机仍无 PostgreSQL 测试库
- 本地质量门禁：73 项通过、12 项数据库/Redis 集成测试因未注入测试连接而跳过，覆盖率 78.68%；Ruff、Mypy strict、OpenAPI/生成文件/兼容性检查通过

## 第五批：Checkpoint 写入代次保护

- `PostgresTurnRepository` 增加 `create_checkpoint`、`update_checkpoint`、`clear_checkpoint`，每次写入先按 Session→Turn 顺序锁行并检查当前 `fence_version` 与活动 Turn
- 新 owner 接管同一 Turn 后，旧 owner 的 Checkpoint 更新和清理均被拒绝；新 owner 仍可在原版本基础上继续记录结果
- 完成一批 Tool 后，`append_tool_round_progress` 校验 Checkpoint 全部结果及模型消息配对，在同一事务内保存本轮消息、递增序号并清理 Checkpoint；下一轮 Tool 可以再创建新的 Checkpoint
- Turn 完成时只追加尚未持久化的消息，并校验已存前缀，防止重试覆盖早前 Tool 结果
- 原有 `PostgresCheckpointRepository` 保留供基础测试/兼容使用，未绑定 Turn 代次；生产运行时接入时必须使用上述 fenced 写入方法
- 当前仍未把 Runner、Redis 租约和 Repository 串成自动恢复链路；外部 Tool 的已发请求也不会因数据库 fencing 自动取消。新增真实 PostgreSQL 接管和多轮原子进度测试已在 GitHub [分支推送检查](https://github.com/royyyyie/pay-agent/actions/runs/35347773800)通过，`quality` 与 `java-contract` 均为绿色
- 本地检查：73 项通过、14 项外部数据库/Redis 测试因缺少测试连接跳过，覆盖率 74.83%；Ruff、Mypy strict、OpenAPI/生成文件/兼容性检查通过

## 第六批：显式启用的持久化 Runner

- `DurableTurnService` 是独立入口，当前只接受 `READ_ONLY` 风险上限；默认 Chat API、现有 `TicketAgentLoop` 不自动切换，避免在恢复协议和故障验收完成前扩大运行面
- 每次调用先取得 Redis Session 租约，再通过 PostgreSQL 建立带幂等键及 fencing 代次的 Turn；已完成的相同请求可返回原结果，进行中的请求拒绝自动接管或重放
- Runner 在模型/Tool 安全点检查并续租，模型给出 Tool 批次后先写 Checkpoint，再执行 Tool；每个结果通过带代次校验的写入独立落库；整批完成后原子追加消息并清理 Checkpoint，最后完成 Turn
- 并发只读 Tool 的结果可乱序完成，但模型消息仍按调用顺序排列；单个结果持久化失败或租约失效时，不进入下一次模型调用。Checkpoint 记录本轮实际模型路由
- 当前没有自动恢复、Pending Queue、取消标记、SSE 续传或运行中外部请求强制取消。若异常发生在 Tool 发出之后，原 Turn 会保持 `running`，同一幂等键重试会被拒绝；须待后续显式恢复流程处理，不能把未知结果当作失败后立即重试
- PostgreSQL/Redis 连接使用逐次建立的基础实现，尚未加入连接池与后台租约保活；长耗时模型或 Tool 调用可能使租约过期，随后在安全点停止。此批不能据此宣称生产可用或严格的端到端多副本串行
- 新增单元测试覆盖 Checkpoint 创建失败、结果持久化失败、写权限上限拒绝；真实 PostgreSQL+Redis 集成测试覆盖正常完成/幂等、提交失败留存 Checkpoint、租约失效后停止。外部集成测试由 GitHub CI 执行，本机缺少测试连接时跳过

## 第七批：显式终结恢复与取消安全点

- `DurableTurnService.recover` 只处理已存在、仍在运行且与原用户输入、授权上下文、幂等键匹配的 Turn；不创建新 Turn。先拿 Redis Session 租约，再通过 PostgreSQL `take_over_turn` 提升 fencing 代次，旧写入者不能继续保存 Checkpoint 或完成 Turn
- 恢复不调用模型或外部 Tool。对仍有 Checkpoint 的批次，保留已确认结果，把未确认结果写为 `TOOL_EXECUTION_UNKNOWN`，原子保存完整 Tool 观察并清除 Checkpoint；随后追加中断说明并完成 Turn。没有 Checkpoint 时，依据原请求与已保存前缀安全终结
- 恢复中途再次失败时，已写入的未知结果仍留在 Checkpoint；重新取得租约后可继续完成，不会重放 Tool。已完成 Turn 通过原幂等请求读取结果，`recover` 本身不重复接管
- 可选 `RedisTurnCancellationStore` 用短期、哈希隔离的 Turn 标记表达取消。`cancel` 必须匹配原请求指纹和 Turn ID；运行时在模型/Tool 安全点检查标记并停止。正在运行的外部网络调用无法强制撤销，取消后仍须显式恢复终结
- 此机制是保守的“终结并允许用户重新提问”，不是自动续写原答案；不会把不明副作用当作已失败。仅允许只读工具的持久化入口，默认 Chat API 尚未接入。Pending Queue、SSE 续传、连接池、后台续租和进程强杀/真实 Java Tool 故障验收仍未完成
- 新增 PostgreSQL/Redis 集成测试覆盖请求绑定、接管、Checkpoint 完整/不完整恢复、无 Checkpoint 恢复、取消前阻止工具派发以及结果不重放；CI 检查完成后记录验收链接

## 阶段退出标准

进程在模型/Tool 安全点中断后可恢复合法协议，Turn 不丢失；多副本中同 Session 不并发；未知写操作不会被重复执行；断线 SSE 可续传。必须有真实 PostgreSQL、Redis 和 Java 链路故障测试证据，单进程测试不能替代。
