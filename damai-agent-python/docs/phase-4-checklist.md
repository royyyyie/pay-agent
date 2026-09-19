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

## 剩余退出标准

- [ ] 建立知识发布、审核、回滚、过期清理和索引别名切换流程
- [ ] 中文语义/混合检索、重排和大规模 Eval；验证召回、引用正确率、延迟和成本
- [ ] 在返回最终推荐前逐候选核验实时票档余量；当前搜索阶段只保证城市、分类、自定义日期和最低票价预算
- [ ] 在硬约束结果上做可解释偏好排序；无合格候选时不得放宽用户条件
- [ ] 推荐离线 Eval、安全红线、A/B 灰度和人工验收
- [ ] 测试专用环境完成端到端 RAG/Java Trace、故障降级和 SLO 验收

第二批完成代表云检索适配和推荐硬约束已经建立，不代表阶段 4 或生产级知识检索已经完成。
