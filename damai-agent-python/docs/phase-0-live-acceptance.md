# 阶段 0：真实链路一键验收

本验收用于证明真实 Java Program Service 与 Python Agent 同时健康，并且一次 Agent Turn 的 `traceId`、`turnId` 和 Tool Call 已进入 Java 日志。

## 前置条件

1. MySQL、Redis、Nacos、Elasticsearch、Kafka 已就绪，Program Service 的 `/actuator/health` 返回 `UP`。
2. Java 与 Python 配置了相同的 `AGENT_TOOL_API_KEY` / `DAMAI_JAVA_TOOL_API_KEY`。
3. Java Program Service 使用端口 `6086`，Python Agent 使用端口 `9010`。
4. Python 使用 `demo` Provider，以保证验收不依赖外部模型服务。

## 执行

在两个终端分别启动 Java Program Service 和 Python Agent。服务稳定后，在仓库根目录执行：

```powershell
.\damai-agent-python\scripts\verify_live_e2e.ps1
```

如果 Java 从非默认工作目录启动，显式指定日志：

```powershell
.\damai-agent-python\scripts\verify_live_e2e.ps1 `
  -JavaLogPath 'D:\path\to\logs\program-service\log.log'
```

staging/production Profile 还需要传入 Python 内部 API 密钥：

```powershell
.\damai-agent-python\scripts\verify_live_e2e.ps1 `
  -AgentInternalKey $env:DAMAI_AGENT_INTERNAL_API_KEY
```

## 通过标准

脚本退出码为 `0`，并输出：

```text
Status       PASS
JavaHealth   UP
AgentHealth  UP
TurnId       turn-...
TraceId      32 位十六进制值
ToolCalls    search_programs
```

任一健康检查失败、Tool 未执行、Java 未回传 Trace 上下文或日志无法关联 `traceId/turnId` 时，脚本会以非零退出码结束。

## 最近一次验收记录

- 日期：2026-09-15
- 结果：`PASS`
- Java / Agent 健康状态：`UP` / `UP`
- Tool Call：`search_programs`
- Turn：`turn-d5f0b2a5-e1c0-44f1-8b73-ddfa11a6cfdf`
- Trace：`96d40dbd5aa04dd7b8e224cc92d74b9b`
- Java 日志：已同时匹配 Turn 与 Trace
