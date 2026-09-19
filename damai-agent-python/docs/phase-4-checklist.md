# 阶段 4：RAG 与推荐

起点：阶段 3 PR #11 已并入 `main`。阶段 3 的真实环境 SLO、故障注入和供应商账单对账仍是生产准入条件，不因开始阶段 4 而自动通过。

## 目标

只让经过审核、版本化且在有效期内的稳定知识进入模型上下文；所有基于知识的回答都有可验证引用。库存、价格、开售、排队、场次、座位和订单/支付/退款状态继续只由实时 Java Tool 提供。推荐必须先满足城市、日期、预算等硬约束，再做可解释排序。

## 第一批：稳定知识检索与引用门禁

- [x] 冻结知识文档结构：`document_id/version/tenant_id/locale/category/source/effective_from/effective_to`
- [x] 目录大小、文档数、字段长度、HTTPS 来源、来源主机白名单、租户和生效窗口校验
- [x] 本地不可变目录的确定性词法检索、租户隔离、公共知识回退、索引内容版本
- [x] 动态事实保守识别；命中价格、余票、开售、排队、场次、座位或订单状态时不注入 RAG，未调用实时 Tool 的模型回答失败关闭
- [x] 检索片段按不可信数据封装，不允许扩大 Tool Scope；上下文长度和 Top-K 有界
- [x] 成功知识回答必须使用已检索的 `[K编号]`；缺失或伪造引用时失败关闭
- [x] RAG 流式文本在引用校验前不发送，避免无来源内容先到客户端
- [x] API、完成事件和持久化结果返回引用元数据与知识索引版本
- [x] 离线 Eval 支持稳定知识召回和动态事实阻断率红线

启用配置：

```dotenv
DAMAI_AGENT_RAG_ENABLED=true
DAMAI_AGENT_KNOWLEDGE_CATALOG_PATH=/app/config/knowledge.json
DAMAI_AGENT_KNOWLEDGE_SOURCE_HOSTS=help.example.com,venue.example.com
DAMAI_AGENT_RAG_TOP_K=4
DAMAI_AGENT_RAG_MAX_CONTEXT_CHARS=8000
DAMAI_AGENT_RAG_MIN_SCORE=0.01
```

目录是非空 JSON 数组。示例文档：

```json
{
  "document_id": "identity-policy",
  "version": "2026.09",
  "tenant_id": "public",
  "locale": "zh-CN",
  "category": "identity_policy",
  "title": "实名制规则",
  "content": "经过审核的稳定规则正文",
  "source": "https://help.example.com/identity-policy",
  "effective_from": "2026-09-01T00:00:00+08:00",
  "effective_to": null
}
```

目录文件应由审核流水线生成并以只读方式挂载。当前实现不会抓取 `source` URL；该字段只用于引用展示和来源核对。来源白名单是精确主机匹配，不接受凭据 URL、HTTP 或运行时用户提供的地址。

离线评测：

```powershell
python scripts/evaluate_rag.py `
  --catalog config/knowledge.json `
  --eval-set eval/knowledge-eval.json `
  --source-host help.example.com
```

每条 Eval 用例必须二选一：设置 `expected_document_ids` 检查召回，或设置 `must_block_as_dynamic=true` 检查动态事实红线。动态阻断率必须为 100%；正式发布还应按业务确认的集合规模设置 Recall 门槛。

## 第二批：云检索与推荐硬约束

- [x] 云 Elasticsearch 只读检索适配：固定索引别名、API Key、请求/响应大小与超时限制、禁止重定向
- [x] Elasticsearch 查询在服务端过滤租户、Locale 和生效窗口，客户端再次校验结果及来源白名单
- [x] `/ready` 检查知识索引可查询；错误不回传 Elasticsearch 响应正文、地址或凭据
- [x] 提供 `dynamic=strict` 的知识索引 Mapping 和可选只读云环境验收测试
- [x] 从用户原文确定性提取明确预算上限，模型遗漏或放宽 `maxPrice` 时自动注入/收紧
- [x] Java Tool Gateway 对城市、分类、自定义日期和最低票价再次执行候选过滤；预算不合格项不返回模型

Elasticsearch 使用官方 `POST /{index}/_search` API，并用 `bool.filter` 做隔离过滤、`multi_match` 搜索标题和正文。运行时 API Key 只应拥有目标读取别名的 `read` 权限，不得赋予索引管理或写权限。参考 [Search API](https://www.elastic.co/guide/en/elasticsearch/reference/current/search-search.html)、[Query DSL filter context](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-filter-context.html/) 和 [API Key 认证](https://www.elastic.co/docs/api/doc/elasticsearch/authentication)。

```dotenv
DAMAI_AGENT_RAG_BACKEND=elasticsearch
DAMAI_AGENT_ELASTICSEARCH_URL=https://your-deployment.example.com:9243
DAMAI_AGENT_ELASTICSEARCH_API_KEY=<read-only-api-key>
DAMAI_AGENT_ELASTICSEARCH_INDEX_ALIAS=damai-knowledge-read
DAMAI_AGENT_KNOWLEDGE_INDEX_VERSION=knowledge-2026.09.19
DAMAI_AGENT_ELASTICSEARCH_TIMEOUT_SECONDS=3
```

索引结构见 `docs/elasticsearch-knowledge-index.json`。`DAMAI_AGENT_ELASTICSEARCH_INDEX_ALIAS` 只接受精确名称，不允许通配符、多索引或请求路径；staging/production 强制 HTTPS。`KNOWLEDGE_INDEX_VERSION` 是发布流水线提供的不可变版本，写入 Turn 结果用于审计和重放。

云环境只读验收使用独立测试索引和只读 API Key，不会写入或删除数据。配置以下 `DAMAI_TEST_ELASTICSEARCH_*` 环境变量后单独运行 `pytest tests/test_elasticsearch_live_acceptance.py -v`：URL、API Key、索引别名、索引版本、允许的来源主机、查询词、租户和预期文档 ID。凭据只放在本机环境变量或 CI Secret，不写入仓库和测试报告。

## 第三批：实时推荐与知识发布

- [x] 新增 Java `recommend_programs` 复合只读 Tool：硬约束后最多扫描 10 个候选，并批量读取票档避免 N+1
- [x] 只返回至少一个预算内票档余量大于 0 的候选；无合格项时返回空列表，不自动放宽条件
- [x] 在实时余票过滤后按相关度、最低可售价格、最早开演或总余量确定性排序，并返回稳定原因码
- [x] Python 从用户原文识别明确推荐意图与软偏好，把普通搜索调用强制路由到实时推荐 Tool
- [x] 推荐 Tool Call 流在策略改写前保持缓冲，模型和持久化上下文只看到最终执行参数
- [x] 知识发布工具执行目录校验、不可变具体索引创建、Bulk 写入、文档数核对及原子别名切换
- [x] 回滚和清理只接受受控前缀下的精确索引名及二次确认；禁止通配符，并拒绝删除活动索引

发布、回滚、Secret 隔离和保留期流程见[知识索引发布与回滚](phase-4-knowledge-operations.md)。运行时只读 Key 与发布 Key 必须分离。

## 第四批：中文混合检索、重排灰度与 Eval 门禁

- [x] 原始查询、去礼貌语查询和受控中文同义扩展最多形成 3 个词法通道，经归一化 RRF 融合；不比较不同后端不可比的原始分数
- [x] Elasticsearch 单通道同时使用 `multi_match`、标题短语和正文短语信号，并继续执行服务端与客户端双重隔离校验
- [x] Provider 无关的确定性重排只使用基础排名、标题/正文覆盖、完整短语和租户专属信号；候选数最多 20
- [x] 重排按 `sessionKey` 与部署侧实验盐稳定分桶，可配置 0～100% 灰度，不记录原始 Session 值
- [x] 检索事件、OTLP Span 和 Prometheus 暴露有界实验组标签；新增 RAG 准备耗时直方图
- [x] RAG Eval 增加 Recall、MRR、引用精度、引用完整性、禁止文档泄漏、平均/P95 延迟及可配置发布阈值
- [x] 推荐 Eval 增加实时 Tool 路由、预算收紧、软偏好和实时库存核验 100% 红线

运行时配置：

```dotenv
DAMAI_AGENT_RAG_HYBRID_ENABLED=true
DAMAI_AGENT_RAG_RRF_RANK_CONSTANT=60
DAMAI_AGENT_RAG_CANDIDATE_K=12
DAMAI_AGENT_RAG_RERANK_ROLLOUT_PERCENT=10
DAMAI_AGENT_RAG_EXPERIMENT_SALT=<deployment-secret-at-least-16-chars>
```

部分灰度（1～99%）必须设置实验盐；0 为控制组，100 为全量重排。变更百分比或盐会改变分桶，因此一次实验期间必须固定二者，并把知识版本、配置版本和时间窗写入发布记录。混合模式最多发起 3 次只读检索，启用前必须用云测试索引核算延迟和请求成本。

本地发布门禁：

```powershell
python scripts/evaluate_rag.py `
  --catalog tests/fixtures/rag_catalog.json `
  --eval-set tests/fixtures/rag_eval.json `
  --source-host help.example.com `
  --hybrid --rerank `
  --min-recall 0.9 --min-mrr 0.8 --min-precision 0.5 `
  --max-p95-latency-ms 500

python scripts/evaluate_recommendations.py `
  --eval-set tests/fixtures/recommendation_eval.json
```

仓库 Fixture 只验证门禁机制，不代表业务大规模 Eval 已完成。正式集合应由业务方维护，覆盖各租户、Locale、同义表达、无答案问题、越权文档和边界预算；结果需要按控制组/实验组分别留档。

## 第五批：高级语义 RAG 检索树

- [x] 新增 `semantic_text` 中文语义索引模板，标题和正文自动复制到显式推理端点；发布时可绑定经过 Eval 的版本化模型
- [x] `semantic_hybrid` 在单次 Elasticsearch Search 中执行 BM25/短语与 Dense 语义召回，并由服务端 RRF 合并
- [x] 两个召回分支都执行租户、Locale 和有效期预过滤；Python 继续执行结果二次隔离校验
- [x] `semantic_rerank` 在混合候选上增加可配置 Cross-Encoder inference endpoint 与有界排名窗口
- [x] 使用语义高亮的相关子片段构建上下文，引用仍绑定父文档，片段和总上下文均有长度上限
- [x] `/ready` 在语义 Profile 下实际执行语义查询，索引字段或 Embedding endpoint 不可用时拒绝就绪
- [x] API、完成事件、Trace 和 Prometheus 增加有界 `knowledgeProfile`，区分本地/Elastic 词法、语义混合和语义重排
- [x] 保留 `lexical` 兼容 Profile；高级路径默认关闭，未经云 Eval/SLO 验收不会自动切换

## 第六批：自动分块、Embedding 与生产发布门禁

- [x] `semantic_text` 显式固定句子分块大小与重叠，中文边界由 Elasticsearch ICU 分词处理；策略变化强制新建不可变索引
- [x] 发布前读取真实 Inference endpoint 元数据并执行 Embedding 探测，拒绝不存在或任务类型不兼容的端点
- [x] Bulk 按文档数和字节数双上限自动分批；发布后核对父文档数、隐藏向量块、Mapping、向量存储大小和语义查询
- [x] 语义发布拆分为 `stage -> acceptance -> promote`，未评测索引不会自动获得线上读别名
- [x] 云 Eval 必须指向具体索引，输出带 Eval 集哈希的 Recall、MRR、P95、并发吞吐和费用报告
- [x] 实际费用通过云账单/Provider Usage 证据二次证明；缺少索引费用、查询费用、证据编号或费用阈值时失败关闭
- [x] `promote` 校验具体索引、语义 Profile、三类门禁、报告时效和 SHA-256 后才原子切换别名

中文语义索引使用 `docs/elasticsearch-knowledge-index-semantic.json`。发布到新具体索引并完成别名切换后，运行时配置示例：

```dotenv
DAMAI_AGENT_RAG_BACKEND=elasticsearch
DAMAI_AGENT_RAG_RETRIEVAL_PROFILE=semantic_hybrid
DAMAI_AGENT_ELASTICSEARCH_SEMANTIC_FIELD=semantic_content
DAMAI_AGENT_ELASTICSEARCH_RANK_WINDOW_SIZE=50
```

Cross-Encoder 验收通过后才切换：

```dotenv
DAMAI_AGENT_RAG_RETRIEVAL_PROFILE=semantic_rerank
DAMAI_AGENT_ELASTICSEARCH_RERANK_INFERENCE_ID=<versioned-rerank-endpoint>
```

云 Eval 的 API Key 只通过 `DAMAI_EVAL_ELASTICSEARCH_API_KEY` 注入，不放入命令行。它至少需要具体索引 `read` 和集群 `monitor_inference`；直接采集 Mapping 时还需要 `view_index_metadata`。若已有 72 小时内、目标索引和内容版本完全匹配的验收报告，可用 `--semantic-evidence-report` 复用其 Mapping 证据并在新报告中记录 SHA-256，从而让 Eval Key 保持最小权限。验收必须指向暂存的具体索引，不能用可能漂移的活动别名。对同一集合依次运行 `semantic_hybrid` 与 `semantic_rerank`，比较 Recall、MRR、P95、吞吐和实际推理费用：

```powershell
$env:DAMAI_EVAL_ELASTICSEARCH_URL = "https://your-deployment.example.com:9243"
$env:DAMAI_EVAL_ELASTICSEARCH_API_KEY = "<read-only-eval-key>"
$env:DAMAI_EVAL_ELASTICSEARCH_INDEX = "damai-knowledge-read-v-20260919-001"
$env:DAMAI_EVAL_ELASTICSEARCH_INDEX_VERSION = "knowledge-2026.09.19-semantic"

python scripts/evaluate_rag.py `
  --backend elasticsearch `
  --retrieval-profile semantic_hybrid `
  --index-name damai-knowledge-read-v-20260919-001 `
  --eval-set C:\secure\knowledge-eval.json `
  --source-host help.example.com `
  --semantic-evidence-report C:\secure\previous-rag-benchmark.json `
  --rank-window-size 20 `
  --benchmark-repetitions 10 --benchmark-concurrency 8 `
  --min-benchmark-requests 100 --min-throughput-qps 10 `
  --min-recall 0.9 --min-mrr 0.8 --min-precision 0.5 `
  --max-p95-latency-ms 800 `
  --report-out C:\secure\rag-benchmark.json
```

费用二次证明与晋级命令见[知识索引发布与回滚](phase-4-knowledge-operations.md)。Elasticsearch Search 响应不提供统一的跨供应商账单金额，因此工具不会把字符数估算伪装成真实费用；最终报告必须引用同一窗口的 Elastic Billing 或推理供应商 Usage 导出。

### 2026-09-19 Elastic Cloud Serverless 实测

- Cloud Serverless 9.6.0、Enterprise 许可证和 API Key 鉴权通过；根端点、Inference 元数据和真实推理均可用。
- `.jina-embeddings-v5-text-small` 返回 1024 维向量；`.jina-reranker-v3.5` 把实名证件规则排在第一位。
- 暂存索引 `damai-knowledge-read-v-cloud-20260919-001` 成功写入 3 个隔离测试父文档，`semantic_text` 自动分块、Embedding、向量存储和语义查询通过；未切换 `damai-knowledge-read` 别名。
- `semantic_hybrid`：Recall 1.0、MRR 1.0、动态拦截率 1.0；负载 P95 1211.515 ms、3.472 QPS，且只有 20 个真实检索请求。
- `semantic_rerank`：30 个真实检索请求，Recall 1.0、MRR 1.0、动态拦截率 1.0；负载 P95 1434.049 ms、5.860 QPS。
- 将排名窗口从 50 收紧到 12、并发提高到 16 后，吞吐提升到 9.418 QPS，但负载 P95 上升到 1812.109 ms；不通过增加并发掩盖尾延迟问题。
- 后续提供的数据面只读 Key 已通过身份认证，并具有目标索引 `read`；但 `monitor_inference=false`、`view_index_metadata=false`，语义查询返回 403。该 Key 对 Cloud 管理 API 返回 401，因此也不能读取组织账单。门禁已在压测前输出缺失权限，不会把普通文档读取误报为语义验收通过。
- 仓库资产复核只发现 3 条隔离测试文档和 5 条 Eval（其中 2 条检索、3 条动态阻断），未发现经过业务方审定、带来源和相关性标签的大规模知识目录；不得把测试 Fixture 记为正式业务集合。
- 结论：功能和样例质量通过；原生产门槛 P95 ≤ 800 ms、吞吐 ≥ 10 QPS 未通过，Cloud Billing 费用证据和业务大规模集合仍缺失，因此保持 staged，不执行 promote。

高级架构与不采用 GraphRAG/RAPTOR 作为默认路径的理由见 [ADR-011](adr/011-advanced-rag-retrieval.md)。

## 剩余退出标准

- [ ] 使用受保护的云凭据执行 `stage`，以真实账单完成语义索引 Recall、MRR、P95、吞吐和费用验收，再执行 `promote`
- [ ] 用业务方大规模集合运行 RAG/推荐 Eval，完成控制组与实验组统计显著性、人工相关性和安全验收
- [ ] 测试专用环境完成端到端 RAG/Java Trace、故障降级和 SLO 验收

第六批完成代表自动分块、Embedding、向量存储验证和失败关闭的云发布门禁已经建立；未留存真实云报告、账单证据和审批回执前，仍不得宣称云 SLO 或生产准入已经完成。
