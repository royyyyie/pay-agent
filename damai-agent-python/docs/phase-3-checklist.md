# 阶段 3：Provider 与可观测性

起点：阶段 2 的 PR #8 已并入 `main`。阶段 2 的真实 Java Gateway 故障注入和云端部署安全核对仍是独立的生产准入条件，不因开始阶段 3 而自动通过。

## 目标

只读链路具备有界、可解释的模型故障处理，以及可关联的 Token、延迟、错误与工具审计指标。最终要在测试专用环境通过 Provider/Java/Redis/PostgreSQL 故障注入，并以实际测量结果评估只读链路 SLO。

## 第一批：安全模型路由

- [x] 识别暂时性网络错误与 HTTP 408/429/500/502/503/504；认证、参数和协议错误不自动重试
- [x] 可配置 0～3 次主模型重试、指数退避；默认 0 次以保持现有行为
- [x] 可选备用 OpenAI-compatible 模型；主模型暂时性故障且重试耗尽时最多切换一次
- [x] 流式响应只在尚未发出任何事件时允许重试/切换；已发出文本、Tool Call 或 Usage 后失败即停止，不拼接两个模型流
- [x] 错误信息不带上游响应正文、URL 或凭据；生产配置拒绝弱备用密钥
- [x] 单元测试覆盖次数上限、备用路由、不可重试错误、部分流不重放和取消传播

`DAMAI_LLM_MAX_RETRIES`、`DAMAI_LLM_RETRY_BACKOFF_SECONDS` 控制重试；`DAMAI_LLM_FALLBACK_BASE_URL`、`DAMAI_LLM_FALLBACK_API_KEY`、`DAMAI_LLM_FALLBACK_MODEL` 必须一起配置才能启用备用模型。备用模型必须支持相同 Tool Schema 和 Chat Completions 流式语义；模型请求即使没有成功返回也可能产生费用，开启重试前须设置成本预算。此批不重试 Java Tool，也不允许模型失败后重新执行 Tool。

## 后续批次与退出标准

- [x] 第二批：版本化模型定价表、单 Turn Token/估算费用阈值；缺少 Usage 或定价时在工具派发前默认拒绝
- [x] 第二批：返回整数微美元成本，提供只含固定标签的实例级 Prometheus 次数、耗时、Token 与估算费用指标；生产 `/metrics` 受内部密钥保护
- [ ] 租户级共享额度、供应商账单对账和失败/重试请求的费用归集
- [ ] OpenTelemetry Trace、跨 Java/Provider 的关联与持久化脱敏 Tool Audit
- [ ] Provider/Java/Redis/PostgreSQL 的隔离故障注入与可重复验收报告
- [ ] 只读链路 SLO 定义、测量、告警和 Runbook；真实测试环境灰度验收

第一批仅建立路由安全策略，不代表阶段 3 完成或生产 SLO 达标。

## 第二批：预算与指标的边界

- `DAMAI_LLM_PRICING_JSON` 以实际 `modelRoute` 为键，每项包含 `version`、`prompt_micro_usd_per_million` 和 `completion_micro_usd_per_million`。金额全程使用整数微美元，按单次模型响应向上取整；缓存 Token 暂按普通 prompt Token 计费，不宣称与供应商账单一致。
- `DAMAI_AGENT_MAX_TURN_TOKENS`、`DAMAI_AGENT_MAX_TURN_COST_MICRO_USD` 默认 0（关闭）。非零时在每次模型响应后检查；超限、Usage 缺失或费用阈值开启但定价路由缺失时，停止后续 Tool 派发。单次模型响应已生成的 Token 无法事前精确截断，故此为后置安全点预算，不是绝对支出上限。
- `/metrics` 输出当前进程的 Prometheus 文本格式；仅含 `turn/model/tool` 和 `success/error` 固定标签，不含租户、用户、Session、Prompt、Tool 参数或模型错误正文。生产须使用内部密钥并在网络层限制采集来源；多副本聚合交由监控系统，不把单实例指标当作全局额度。
- 当前 Tool Audit 仍是元数据日志/可注入 Sink，未具备不可篡改或持久保留保证。OTel、租户配额、端到端故障注入和 SLO 均未完成。
