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

- [ ] Token/费用治理：模型定价版本、单 Turn 预算、租户额度和实际成本汇总
- [ ] OpenTelemetry Trace、核心 Metrics、跨 Java/Provider 的关联与脱敏 Tool Audit
- [ ] Provider/Java/Redis/PostgreSQL 的隔离故障注入与可重复验收报告
- [ ] 只读链路 SLO 定义、测量、告警和 Runbook；真实测试环境灰度验收

第一批仅建立路由安全策略，不代表阶段 3 完成或生产 SLO 达标。
