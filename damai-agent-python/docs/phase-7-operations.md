# 阶段 7：灰度、排空与回滚 Runbook

## 1. 部署前

1. 合并到 `main` 后等待 `Damai Agent Supply Chain` 全绿。`publish` Job 会发布 `ghcr.io/<owner>/<repo>/damai-agent:sha-<40位提交>`，并输出不可变 digest、SBOM 和 GitHub 来源证明；部署时只使用 digest。
2. 由 Secret Manager 注入 `damai-agent-secrets`，不要提交 `secret.example.yaml` 的实际副本。
3. 把 ConfigMap 中 `.example.invalid` 地址和模型名替换成已经验收的内部端点。
4. 生产固定 `durable` runtime 和持久化审计；先执行阶段 2、3 数据库迁移。
5. 第一轮只读灰度保持监控、购买意向和订单提交三个功能开关为 `false`。
6. 确认 `terminationGracePeriodSeconds` 大于应用 shutdown timeout 与 `preStop` 之和。

## 2. 探针语义

- `/livez`：仅进程存活。下游故障时仍应返回 200，避免重启风暴。
- `/readyz`：排空、PostgreSQL、Redis、审计库或启用中的 RAG 不可用时返回 503；平台应停止向该 Pod 路由新流量。
- `/health`、`/ready`：仅为旧部署兼容；新部署禁止继续引用。

必须告警：ready Pod 数不足、`damai_agent_accepting_requests=0` 持续但 Pod 未退出、`damai_agent_drain_timeouts_total` 增长、在途请求长期不归零。

`DAMAI_AGENT_MAX_CONCURRENT_REQUESTS` 是每个进程的最后一道并发硬上限，不替代 BFF/网关的租户限流。达到上限时 Uvicorn 直接返回 503，请以 Staging 压测结果和下游连接池容量确定该值。

## 3. Staging 压测

仅在批准的 Staging 只读兼容入口运行：

```powershell
$env:DAMAI_AGENT_LOADTEST_API_KEY = "<从 Secret Manager 临时注入>"
uv run --frozen python scripts/load_readonly.py `
  --url https://staging-agent.example.com/api/v1/chat `
  --requests 100000 --concurrency 25 `
  --duration-seconds 1800 --rate-rps 20 --min-requests 1000 `
  --min-success-rate 0.995 --max-p95-ms 3000 `
  --report-out artifacts/readonly-probe.json
Remove-Item Env:DAMAI_AGENT_LOADTEST_API_KEY
```

脚本不打印正文和凭据。持续时间模式在时间窗结束后停止发送新请求，已发出的请求完成后生成起止时间、吞吐和分位数；提前用完 `--requests` 上限会判为失败。该脚本仅适用于仍启用的只读兼容入口，不能作为正式 durable 链路的主验收证据。正式入口必须从 Java BFF 发起，每个请求生成新的短时 Delegation、Turn ID 和 Idempotency-Key；不要复用静态签名模拟业务流量。主验收报告还需保留提交、镜像 digest、配置版本、CPU、内存、连接池和 BFF 到 Python 的端到端结果。

## 4. 排空验收

1. 建立普通请求和 SSE 长连接，确认在途指标大于 0。
2. 删除一个 Pod 或触发滚动发布。
3. 确认 Pod 进入终止态后不再收到新业务请求；直接命中时返回 503、`SERVICE_DRAINING` 和 `Retry-After`。
4. 已准入 SSE 应在响应结束前保持在途计数；完成后归零。
5. 超过排空时间的 Turn 必须依靠 durable Checkpoint 安全恢复，不能重放未知写 Tool。

## 5. 灰度与回滚

按 1%、5%、25%、50%、100% 逐档放量。每档核对成功率、P95/P99、Provider/Tool 错误率、费用、队列、连接池、CPU/内存、引用红线和租户隔离。任何退出红线非零立即停止。

回滚时先把流量权重降为 0，再回退到上一镜像 digest。数据库只采用 Expand/Migrate/Contract：旧版本仍在运行时不得删除列或表。知识索引使用阶段 4 的别名回滚；订单和监控状态禁止手工更新绕过状态机。

## 6. 证据包

归档 CI 链接、镜像 digest、SBOM/签名、扫描结果、压测原始报告、指标窗口、脱敏 Trace、故障注入、排空、备份恢复、回滚、阶段 3～6 外部报告和审批回执。任何报告必须可追溯到同一提交和配置版本。

先复制 `docs/phase7-release-manifest.example.json`，填入真实 Staging 数据。示例中的零提交、零 digest、失败场景和未批准状态是有意设计，不能直接用于放量。阶段 3～6 文件必须与 Manifest 中的 SHA-256 完全一致。生成证据声明：

```powershell
uv run --frozen python scripts/attest_phase7_release.py `
  --manifest artifacts/phase7-release-manifest.json `
  --metrics-evidence artifacts/metrics.json `
  --traces-evidence artifacts/traces.json `
  --faults-evidence artifacts/faults.json `
  --rollback-evidence artifacts/rollback.json `
  --security-evidence artifacts/security.json `
  --approvals-evidence artifacts/approvals.json `
  --phase3-evidence artifacts/phase3.json `
  --phase4-evidence artifacts/phase4.json `
  --phase5-evidence artifacts/phase5.json `
  --phase6-evidence artifacts/phase6.json `
  --report-out artifacts/phase7-release-attestation.json
```

生成成功不等于可放量：脚本只有在 30 分钟/1000 请求、成功率、P95、Trace、供应链、七类故障、四类回滚、安全红线、阶段 3～6 继承证据和四方审批全部通过时才返回 0。部署控制器必须再次绑定即将部署的提交和镜像 digest：

```powershell
uv run --frozen python scripts/verify_phase7_release.py `
  --attestation artifacts/phase7-release-attestation.json `
  --expected-commit '<40位小写提交 SHA>' `
  --expected-image-digest 'sha256:<64位镜像 digest>' `
  --metrics-evidence artifacts/metrics.json `
  --traces-evidence artifacts/traces.json `
  --faults-evidence artifacts/faults.json `
  --rollback-evidence artifacts/rollback.json `
  --security-evidence artifacts/security.json `
  --approvals-evidence artifacts/approvals.json `
  --phase3-evidence artifacts/phase3.json `
  --phase4-evidence artifacts/phase4.json `
  --phase5-evidence artifacts/phase5.json `
  --phase6-evidence artifacts/phase6.json
```

把验证命令作为 `production-canary` 环境的前置 Job，并在 GitHub 仓库 Settings → Environments 中为该环境配置必需审批人。仓库 CODEOWNERS 不能代替分支保护；`main` 应要求 `quality`、`java-contract`、`source-security`、`image-security` 全部通过并禁止绕过。
