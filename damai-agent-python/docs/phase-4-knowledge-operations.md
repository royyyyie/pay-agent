# 阶段 4：知识索引发布与回滚

本文只适用于经过审核的稳定知识。价格、余票、场次、座位、开售和订单状态不得发布到知识索引。

## 账号隔离

运行时和发布时必须使用两个不同的 API Key：

- Agent 运行时 Key 只允许读取 `damai-knowledge-read` 别名。
- Eval Key 只允许读取待验收的精确具体索引，并具有读取 Mapping 所需的 `view_index_metadata`；不得切换别名或写入索引。
- 发布 Key 仅由受保护的发布流水线持有，权限范围限制在 `damai-knowledge-read` 别名及 `damai-knowledge-read-v-*` 具体索引。它需要创建索引、Bulk 写入、Count/Stats/Mapping 校验、读取/切换别名，以及在保留期后删除旧索引的权限；语义发布还需要 `monitor_inference` 和使用指定推理端点的权限。

发布 Key 不得写入 `.env`、仓库、构建产物或应用运行环境，也不接受命令行参数，避免进入 Shell 历史和进程列表。Elastic Stack 与 Serverless 的权限名称可能不同，应按实际部署使用最小权限验证；操作依据见 [Create index API](https://www.elastic.co/guide/en/elasticsearch/reference/current/indices-create-index.html)、[Bulk API](https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-bulk.html/) 和 [Aliases](https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html)。

## 发布流程

1. 内容负责人审核目录中的正文、来源、租户、Locale、生效时间和失效时间。
2. 在不接触云凭据的情况下验证目录：

```powershell
cd damai-agent-python
uv run --frozen python scripts/manage_knowledge_index.py validate `
  --catalog C:\secure\knowledge-20260919.json `
  --source-host help.example.com `
  --source-host venue.example.com
```

3. 记录输出的 `contentVersion`，完成双人审批并生成不可变小写索引名，例如 `damai-knowledge-read-v-20260919-001`。
4. 词法索引使用默认 Mapping；高级语义 RAG 使用 `docs/elasticsearch-knowledge-index-semantic.json`。语义 Mapping 显式固定中文 Embedding endpoint 和 `sentence/200/overlap=1` 分块策略。修改端点、分块或向量索引参数均等同于新模型发布，必须创建新索引并重新评测。
5. 在受保护的发布环境中设置凭据并暂存索引。`--confirm-index` 必须与 `--index` 完全一致。`stage` 只创建具体索引，不会切换线上别名：

```powershell
$env:DAMAI_KNOWLEDGE_PUBLISH_URL = "https://your-deployment.example.com:9243"
$env:DAMAI_KNOWLEDGE_PUBLISH_API_KEY = "<publisher-key>"

uv run --frozen python scripts/manage_knowledge_index.py stage `
  --catalog C:\secure\knowledge-20260919.json `
  --source-host help.example.com `
  --source-host venue.example.com `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260919-001 `
  --confirm-index damai-knowledge-read-v-20260919-001 `
  --mapping docs/elasticsearch-knowledge-index-semantic.json `
  --serverless `
  --semantic-inference-id eis-microsoft-multilingual-e5-large `
  --chunking-strategy sentence `
  --max-chunk-size 200 `
  --chunk-overlap 1
```

模板内置可自托管的中文基线 `.multilingual-e5-small-elasticsearch`。`--semantic-inference-id` 会在内存中绑定经过评测的版本化端点而不改写模板；示例端点是否可用取决于 Elastic Cloud 区域和许可。工具在创建索引前读取端点元数据并发起一次真实 Embedding；Bulk 写入触发 `semantic_text` 自动分块、Embedding 和向量存储。随后工具核对父文档数、隐藏向量块数、实际 Mapping、存储大小并执行语义查询冒烟测试。任一步失败都不会切换读别名。

工具默认读取根端点的 `version.build_flavor` 自动识别 Elastic Cloud Serverless；根端点受代理限制时，可用 `--serverless` 或 `--stateful` 显式指定并跳过探测。Serverless 会托管分片和副本拓扑，工具只会从内存中的索引定义移除 `number_of_shards` 和 `number_of_replicas`，不会改写受审模板。Serverless 不开放索引 `_stats`，因此回执中的 `vectorChunkCount` 为 `null`，发布门禁改为核对父文档数量、实际 `semantic_text` Mapping，并要求真实语义查询至少命中一条；Stateful 部署仍额外核对隐藏向量块计数和存储大小。回执会记录 `deploymentMode`，供审批系统确认发布目标符合预期。

Bulk 按 500 文档/5 MiB 双上限自动分批，最后一批等待刷新。发布账号还需要使用指定 inference endpoint 的最小权限和容量；运行时只读账号不得获得索引写权限。失败响应正文不会进入异常消息。

6. 使用只读 Eval Key 对“具体索引”而非活动别名执行质量和负载验收。语义查询会调用 Inference API，因此最小权限为目标具体索引的 `read` 和集群级 `monitor_inference`；直接读取 Mapping 时还需要 `view_index_metadata`。如果发布/上一轮验收已经留下 72 小时内、目标索引和内容版本完全一致的报告，可以通过 `--semantic-evidence-report` 复用其中的 Mapping 证据。新报告会记录旧报告 SHA-256，不要求 Eval Key 获得 `view_index_metadata`，但仍强制要求 `monitor_inference`。至少 30 个实测请求；正式集合应远大于该下限：

```json
{
  "cluster": ["monitor_inference"],
  "indices": [
    {
      "names": ["damai-knowledge-read-v-20260919-001"],
      "privileges": ["read"]
    }
  ]
}
```

```powershell
$env:DAMAI_EVAL_ELASTICSEARCH_URL = "https://your-deployment.example.com:9243"
$env:DAMAI_EVAL_ELASTICSEARCH_API_KEY = "<read-only-eval-key>"
$env:DAMAI_EVAL_ELASTICSEARCH_INDEX = "damai-knowledge-read-v-20260919-001"
$env:DAMAI_EVAL_ELASTICSEARCH_INDEX_VERSION = "knowledge-2026.09.19-semantic"

uv run --frozen python scripts/evaluate_rag.py `
  --backend elasticsearch `
  --retrieval-profile semantic_hybrid `
  --index-name damai-knowledge-read-v-20260919-001 `
  --index-version knowledge-2026.09.19-semantic `
  --eval-set C:\secure\knowledge-eval.json `
  --source-host help.example.com `
  --semantic-evidence-report C:\secure\previous-rag-benchmark.json `
  --rank-window-size 20 `
  --benchmark-repetitions 10 `
  --benchmark-concurrency 8 `
  --warmup-requests 10 `
  --min-benchmark-requests 100 `
  --min-recall 0.90 `
  --min-mrr 0.80 `
  --max-p95-latency-ms 800 `
  --min-throughput-qps 10 `
  --report-out C:\secure\rag-benchmark.json
```

无费用证据时报告会保留 Recall、MRR、P95 和吞吐结果但返回非零，不允许晋级。查询 Elastic/推理供应商账单或 Usage 导出，取得同一测试窗口的索引 Embedding 和查询/重排实际费用后，生成新的费用证明报告：

Elasticsearch 项目 API Key 不能读取组织账单，即使名称是“只读 Key”也不能代替组织级 Cloud Key。自动采集必须另建只读 Elastic Cloud API Key，通过环境变量注入；组织 ID 同样不放入命令行。采集器调用官方 Cloud Billing v2 `costs/instances`，只保留目标项目并记录原始响应 SHA-256：

```powershell
$env:ELASTIC_CLOUD_API_KEY = "<billing-read-cloud-api-key>"
$env:ELASTIC_CLOUD_ORGANIZATION_ID = "<organization-id>"

uv run --frozen python scripts/collect_elastic_billing.py `
  --project-id <serverless-project-id> `
  --from-time 2026-09-19T11:30:00Z `
  --to-time 2026-09-19T12:00:00Z `
  --report-out C:\secure\elastic-billing-20260919.json
```

证据保留 Elastic 原始 ECU 和产品行项目，不自动假设 ECU 与 USD 的换算关系。应按组织合同或 Billing Usage 导出的实际币种分别确认索引窗口和查询窗口费用，再交给费用证明工具。Cloud Key 的创建和权限见 [Elastic Cloud API keys](https://www.elastic.co/docs/deploy-manage/api-keys/elastic-cloud-api-keys)，账单接口见 [Cloud Billing API](https://www.elastic.co/docs/api/doc/cloud-billing/operation/operation-getcostsbyinstancesv2)。

```powershell
uv run --frozen python scripts/attest_rag_cost.py `
  --benchmark-report C:\secure\rag-benchmark.json `
  --report-out C:\secure\rag-acceptance.json `
  --observed-indexing-cost-usd 0.42 `
  --observed-query-cost-usd 0.08 `
  --cost-evidence elastic-billing:usage-export-20260919 `
  --cost-evidence-file C:\secure\elastic-usage-export-20260919.json `
  --max-total-cost-usd 1.00 `
  --max-query-cost-per-1k-usd 1.00
```

7. 只有最终报告的质量、负载和费用三道门禁全部通过，才能原子切换别名：

```powershell
$env:DAMAI_KNOWLEDGE_PUBLISH_URL = "https://your-deployment.example.com:9243"
$env:DAMAI_KNOWLEDGE_PUBLISH_API_KEY = "<publisher-key>"

uv run --frozen python scripts/manage_knowledge_index.py promote `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260919-001 `
  --confirm-index damai-knowledge-read-v-20260919-001 `
  --acceptance-report C:\secure\rag-acceptance.json
```

费用证明报告会记录原始 Benchmark 和账单导出文件的 SHA-256，不复制可能敏感的账单正文。晋级工具校验报告版本、具体索引、语义 Profile、质量/负载/费用结果和 72 小时有效期，并把最终报告 SHA-256 写入回执。通过后保留 stage 回执、原始 Benchmark、账单证据、最终验收报告、审批记录和 promote 回执。

## 回滚

回滚目标必须属于同一受控前缀且包含文档。工具只移除当前实际观察到的具体索引，不使用通配符：

```powershell
uv run --frozen python scripts/manage_knowledge_index.py rollback `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260918-003 `
  --confirm-index damai-knowledge-read-v-20260918-003
```

回滚后重新执行只读验收并记录结果。不要立即删除刚退出的索引，以便再次前滚或调查。

## 过期清理

旧索引至少经过组织规定的回滚保留期、确认无审计/调查用途后，才可按精确名称删除。工具拒绝删除当前别名仍指向的索引：

```powershell
uv run --frozen python scripts/manage_knowledge_index.py delete `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260801-001 `
  --confirm-index damai-knowledge-read-v-20260801-001
```

发布、回滚和删除均是外部状态变更，应由有审批、Secret 保护和审计留痕的专用流水线执行，不由 Agent 运行进程自动触发。
