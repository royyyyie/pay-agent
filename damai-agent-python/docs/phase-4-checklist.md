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

## 剩余退出标准

- [ ] 接入经安全评审的中文向量/语义通道，并在云测试索引完成维度、模型版本、延迟与成本验收
- [ ] 用业务方大规模集合运行 RAG/推荐 Eval，完成控制组与实验组统计显著性、人工相关性和安全验收
- [ ] 测试专用环境完成端到端 RAG/Java Trace、故障降级和 SLO 验收

第四批完成代表混合词法检索、重排灰度和自动化门禁已经建立，不代表语义检索、业务人工验收、云 SLO 或生产准入已经完成。
