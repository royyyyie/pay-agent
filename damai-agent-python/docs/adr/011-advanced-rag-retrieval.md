# ADR-011：生产级高级 RAG 检索树

- 状态：Accepted
- 日期：2026-09-19
- Supersedes：无；扩展 ADR-006 的动态事实边界

## Context

词法检索擅长精确规则、专有名词和编号，但无法稳定覆盖同义表达；单独的 Dense 检索可能弱化关键词约束。应用侧手工管理向量维度、分块、模型调用和分数归一化，也会增加密钥面、迁移成本和运行故障点。

Elasticsearch 当前推荐使用 `semantic_text` 自动完成分块、Embedding 和向量存储，并用 RRF 合并词法与语义排名；Retriever API 可以把召回和 Cross-Encoder 重排组成一棵服务端检索树。[Elastic semantic search](https://www.elastic.co/docs/solutions/search/get-started/semantic-search)、[Retriever overview](https://www.elastic.co/docs/solutions/search/retrievers-overview)

## Decision

采用分层、可降级且可评测的高级 RAG：

1. 知识仍是经过审核的不可变父文档，保留租户、Locale、生效窗口、来源和版本。
2. 语义索引使用 `semantic_text`，由显式版本化的 inference endpoint 自动分块和生成向量。中文基线使用 `.multilingual-e5-small-elasticsearch`；更换模型必须创建新具体索引并重新执行 Eval，不在活动索引上静默替换。
3. 第一阶段同时执行标题/正文 BM25 与 `semantic_text` 检索；两个子检索器都包含相同的租户、Locale 和有效期过滤。
4. Elasticsearch 在同一快照内用 RRF 合并两个排名，不比较不可比的原始分数。[Elastic RRF reference](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)
5. 高质量 Profile 可在 RRF 候选上增加 `text_similarity_reranker` Cross-Encoder；推理端点必须显式配置并纳入延迟、费用和版本审计。[Elastic semantic reranking](https://www.elastic.co/docs/solutions/search/ranking/semantic-reranking)
6. 语义高亮返回相关子片段，模型上下文只装入有界片段；引用仍指向经过审核的父文档。
7. Python 继续执行来源、租户、Locale、生效窗口、响应大小和引用完整性复验。动态库存、价格、场次、座位及订单状态继续只走实时 Java Tool。
8. 提供 `lexical`、`semantic_hybrid`、`semantic_rerank` 三个 Profile。默认保持 `lexical`，只有云索引、推理端点、离线 Eval 和 SLO 验收全部通过后才灰度切换。

## Consequences

- 应用不保存向量模型密钥或手工维护向量维度；索引发布流水线负责触发推理并验证 Bulk 结果。
- 语义 Profile 依赖支持 `semantic_text` 和 Retriever API 的 Elasticsearch 版本、许可及推理容量；缺少能力时 `/ready` 失败，不伪装成语义检索。
- Cross-Encoder 提高相关性但增加延迟和费用，必须在业务集合上证明相对 `semantic_hybrid` 有收益。
- GraphRAG、RAPTOR 或 LLM 生成摘要不作为默认路径。当前知识是短小稳定规则，引入合成知识会扩大审核面；只有多跳知识 Eval 证明必要时，才能通过新 ADR 增加。
