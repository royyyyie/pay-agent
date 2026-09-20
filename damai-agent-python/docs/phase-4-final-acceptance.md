# 阶段 4：最终验收与封板

阶段 4 的功能开发以四份相互绑定的报告封板，任何一份缺失、过期、目标索引不一致或审批不完整，`promote` 都会在切换别名前失败关闭：

1. `rag-acceptance.json`：具体暂存索引的质量、负载和真实费用报告。
2. `rag-experiment.json`：同一 Eval、同一索引上的控制组/实验组配对统计与盲审证据。
3. `recommendation-acceptance.json`：绑定当前 Tool OpenAPI 契约的正式推荐安全 Eval。
4. `phase4-slo-attestation.json`：测试环境 SLO、RAG/Java Trace、四类故障和回滚演练证明。

模板均为 `draft` 或失败值，只用于说明结构，不能作为验收证据。不要把 Prompt、Tool 正文、用户信息、API Key 或原始账单复制进模板和报告。

## 1. RAG 控制组与实验组

使用同一个经审批的 `damai.rag.eval/v1` Bundle，分别运行控制 Profile 和候选 Profile。两份报告必须指向同一具体索引、同一目录 SHA-256 和同一 Eval 文件。正式报告会输出不含查询正文的逐 Case 指标，用于配对统计。

由至少两名评审按盲审协议完成相关性和安全复核，把 [人工复核模板](rag-human-review.example.json)复制到受保护目录并填写真实结果。然后执行：

```powershell
uv run --frozen python scripts/compare_rag_experiment.py `
  --baseline-report C:\secure\rag-baseline.json `
  --candidate-report C:\secure\rag-candidate.json `
  --review-evidence C:\secure\rag-human-review.json `
  --mode non_inferiority `
  --margin 0.02 `
  --confidence 0.95 `
  --bootstrap-samples 5000 `
  --report-out C:\secure\rag-experiment.json
```

`non_inferiority` 要求候选相对控制组的配对 Bootstrap 单侧置信下界不低于负向容忍边界，且不能出现任何红线回退。确需证明提升时使用 `superiority`；此时置信下界必须大于零，精确单侧符号检验也必须达到相同显著性水平。算法种子由两份报告哈希确定，重复运行可复现。

## 2. 正式推荐 Eval

从 [推荐 Eval 模板](recommendation-eval-bundle.example.json)建立至少 100 条互不重复的业务案例，覆盖预算、城市、日期、偏好、无结果、实时余票核验和安全关键边界。`contract_sha256` 必须是当前 `contracts/agent-tools-v1.openapi.yaml` 的 SHA-256。

```powershell
uv run --frozen python scripts/evaluate_recommendations.py `
  --eval-set C:\secure\recommendation-eval.json `
  --require-approved-eval `
  --min-eval-cases 100 `
  --report-out C:\secure\recommendation-acceptance.json
```

路由、预算收紧、软偏好和实时核验均为 100% 红线。报告同时按业务分类和风险等级输出结果。

## 3. 测试环境 SLO、Trace、故障和回滚

在测试专用环境运行至少 10 分钟、100 个请求，保留指标导出、脱敏 Trace 导出和故障演练记录。四类故障必须全部覆盖：Provider、Java Gateway、Redis、PostgreSQL。未知 Tool 结果不得自动重放，重复副作用必须为零；还要完成灰度回滚演练和运维/安全审批。

把 [SLO Manifest 模板](phase4-slo-manifest.example.json)复制到受保护目录并填入实测值，然后只对原始证据计算哈希：

```powershell
uv run --frozen python scripts/attest_phase4_slo.py `
  --manifest C:\secure\phase4-slo-manifest.json `
  --metrics-evidence C:\secure\prometheus-export.json `
  --trace-evidence C:\secure\trace-export.json `
  --fault-evidence C:\secure\fault-and-rollback-record.json `
  --report-out C:\secure\phase4-slo-attestation.json
```

硬门槛为可用性不低于 99%、Turn P95 不高于 10 秒、至少 20 个连续 Trace 样本、至少 10 个 RAG 到 Java 的关联 Trace，以及四类故障全部通过。

## 4. 费用签证与最终晋级

候选原始 Benchmark 通过质量、负载、Eval 治理和实验比较后，按[知识索引发布与回滚](phase-4-knowledge-operations.md)采集真实账单并执行 `attest_rag_cost.py`。费用签证生成的 `benchmarkReportSha256` 必须与实验报告的 `candidateReportSha256` 一致。

最后执行：

```powershell
uv run --frozen python scripts/manage_knowledge_index.py promote `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260920-001 `
  --confirm-index damai-knowledge-read-v-20260920-001 `
  --acceptance-report C:\secure\rag-acceptance.json `
  --experiment-report C:\secure\rag-experiment.json `
  --recommendation-report C:\secure\recommendation-acceptance.json `
  --slo-report C:\secure\phase4-slo-attestation.json
```

晋级工具先验证四条证据链，再调用 Elasticsearch 原子切换别名；因此证据错误不会产生部分发布。成功回执记录四份报告的 SHA-256，和 stage 回执、账单导出、审批记录一起归档。

## 阶段状态定义

- 工程封板：代码、自动化门禁、模板、单元/集成测试和操作文档全部通过。
- 测试环境验收：四份真实报告通过，但尚未切换生产别名。
- 生产准入：四份报告通过、审批完成并成功执行 `promote`。

没有真实业务数据、账单、Trace、故障和审批证据时，只能声明“工程封板”，不能声明测试环境验收或生产准入。
