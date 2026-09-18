# 阶段 2 持久化入口操作说明

此入口只允许官方授权的只读 Tool。不要把阶段 2 的完成理解为写操作准入，亦不要用生产业务库执行测试迁移或故障注入。

## 部署顺序

1. 使用独立迁移账号依次执行 `migrations/001_agent_active_checkpoint.sql`、`002_agent_session_turn.sql`、`003_agent_turn_event.sql`。迁移前备份，并为消息、Checkpoint、事件表设置最小权限、传输/静态加密、备份与保留期。
2. 安装 `postgres` 与 `redis` 可选依赖；设置 `DAMAI_AGENT_RUNTIME_BACKEND=durable`、PostgreSQL DSN、Redis URL、至少 32 字符的 `DAMAI_AGENT_DELEGATION_HMAC_KEY`，以及模型、Java Tool、内部 API 密钥。密钥不得提交到 Git。
3. 在隔离环境先跑 CI 的 PostgreSQL/Redis 测试和三个真实 Java 只读 Tool 验收，再灰度开通 `/api/v2/turns`。持久化模式下旧 `/api/v1/chat` 返回 410，不会静默退回内存。
4. 对每个请求生成稳定的 `Idempotency-Key` 和 `turnId`；失败后重试必须使用原始正文、用户、Session、Scope 和幂等键。签名委托可重新签发，但身份字段必须保持一致。

`GET /health` 是不探测外部依赖的存活检查；`GET /ready` 会验证 PostgreSQL 的五张运行时表和 Redis 连接，缺少迁移或依赖不可用时返回 503。灰度放量前应以 `/ready` 为准。

## 签名委托

内部调用方发送 `X-Agent-Delegation`（无 padding 的 base64url JSON）与 `X-Agent-Delegation-Signature`（对前者 ASCII 字节计算 HMAC-SHA256 后的小写十六进制）。JSON 必须包含 `tenantId`、`userId`、`sessionKey`、`turnId`、`requestId`、`traceId`、`locale`、`channel`、`toolScopes`、`riskCeiling: "READ_ONLY"`、`delegationTokenId`、Unix 秒 `issuedAt` 和 `expiresAt`。有效期最长 300 秒，服务端不接受过期或超前过多的签名。

签名密钥只交给受信的 Java 网关/身份服务，不暴露给浏览器。生产环境还要求 `X-Agent-Internal-Key`；网络层应限制 Agent API 的调用来源。委托只保证请求字段未被篡改，不能替代 Java 侧的用户鉴权与 Scope 授权。

## 请求、排队与恢复

- `POST /api/v2/turns`：正文为 `{"message":"...","sessionKey":"..."}`，另带 `Idempotency-Key` 和委托头。成功返回 Turn 结果；Session 正被占用时返回 202、`position` 和 `turnId`。请求方保留原正文并稍后以同一幂等键重试。队列容量和 TTL 由服务端限制，过期后需重新入队。
- `POST /api/v2/turns/cancel`：同样的正文、幂等键和委托；仅对匹配的活动 Turn 标记取消，运行时在下一个安全点停止。外部 Java 请求已发出时不保证立即撤销。
- `POST /api/v2/turns/recover`：原请求仍为 `running` 且原租约已释放/过期时调用。若调用方保留原正文，可发送同原请求；若正文已丢失，可只发送 `sessionKey`，但签名委托必须包含原 `turnId`，服务端从建立 Turn 时原子保存的用户消息读取正文并重新核对请求指纹。恢复先接管数据库代次，把未确认的 Tool 结果标为未知，再终结 Turn；不会调用模型或重新执行 Tool。已完成的 Turn 应重发原 `POST /api/v2/turns` 取得幂等结果。
- 崩溃后若 Redis 不可用或数据库代次/Checkpoint 校验失败，保持原 Turn `running`，不得绕过恢复流程手动清理行或盲目重发工具。

## SSE 续传

`GET /api/v2/turns/{turnId}/events?sessionKey=...` 使用相同委托头和内部密钥。断线后带 `Last-Event-ID: <eventSeq>` 再连接，只返回其后的事件；首次连接不带该头。事件的 `id:` 为 Turn 内单调数字。SSE 断线不取消 Turn，流最长 300 秒；超时后重新签发委托并续传。完成事件与 Turn 提交同事务，不会出现“已完成但无终结事件”的正常提交状态。

## 运维边界

当前队列是有界的“准入提示”，不是独立 Worker；客户端遗失正文时，Redis token 无法重建请求。PostgreSQL 连接按操作建立，尚需按部署并发量评估连接池/PgBouncer 容量；SSE 重连目前轮询数据库，需监控查询量。事件可能含用户输入和 Tool 数据，生产应实施保留期清理，清理后旧 `Last-Event-ID` 不再保证可重放。CI 已在隔离服务中做进程强杀与多实例竞争测试；真实 Java 链路故障注入仍须在专门环境验收，不得在云端业务命名空间直接实施。

## 真实 Java Tool 验收输入

只在测试专用 Java Gateway 上设置 `DAMAI_TEST_JAVA_BASE_URL`、`DAMAI_TEST_JAVA_TOOL_API_KEY`、`DAMAI_TEST_JAVA_PROGRAM_ID`、`DAMAI_TEST_JAVA_KEYWORD`，运行 `pytest tests/test_java_live_acceptance.py`。测试依次查询三个只读 Tool，并验证错误参数与错误密钥被拒绝；未设置这些变量时自动跳过。不要把密钥写进文档、PR 或聊天；该验收不会故意更改票务数据，也不替代受控的网络故障注入和 Java 侧日志/Trace 核对。
