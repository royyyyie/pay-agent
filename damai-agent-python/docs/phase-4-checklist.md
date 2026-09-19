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

## 后续批次与退出标准

- [ ] 接入生产级检索后端（建议沿用云 Elasticsearch），保持相同租户、有效期和来源白名单契约
- [ ] 建立知识发布、审核、回滚、过期清理和索引别名切换流程
- [ ] 中文语义/混合检索、重排和大规模 Eval；验证召回、引用正确率、延迟和成本
- [ ] 基于 Java 实时候选集实现城市、日期、预算、可售状态等硬约束过滤
- [ ] 在硬约束结果上做可解释偏好排序；无合格候选时不得放宽用户条件
- [ ] 推荐离线 Eval、安全红线、A/B 灰度和人工验收
- [ ] 测试专用环境完成端到端 RAG/Java Trace、故障降级和 SLO 验收

第一批完成代表 RAG 安全骨架可用，不代表阶段 4 或生产级知识检索已经完成。
