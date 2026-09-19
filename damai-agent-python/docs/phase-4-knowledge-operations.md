# 阶段 4：知识索引发布与回滚

本文只适用于经过审核的稳定知识。价格、余票、场次、座位、开售和订单状态不得发布到知识索引。

## 账号隔离

运行时和发布时必须使用两个不同的 API Key：

- Agent 运行时 Key 只允许读取 `damai-knowledge-read` 别名。
- 发布 Key 仅由受保护的发布流水线持有，权限范围限制在 `damai-knowledge-read` 别名及 `damai-knowledge-read-v-*` 具体索引。它需要创建索引、Bulk 写入、Count 校验、读取/切换别名，以及在保留期后删除旧索引的权限。

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
4. 在受保护的发布环境中设置凭据并发布。`--confirm-index` 必须与 `--index` 完全一致，避免误操作其他索引：

```powershell
$env:DAMAI_KNOWLEDGE_PUBLISH_URL = "https://your-deployment.example.com:9243"
$env:DAMAI_KNOWLEDGE_PUBLISH_API_KEY = "<publisher-key>"

uv run --frozen python scripts/manage_knowledge_index.py publish `
  --catalog C:\secure\knowledge-20260919.json `
  --source-host help.example.com `
  --source-host venue.example.com `
  --alias damai-knowledge-read `
  --index damai-knowledge-read-v-20260919-001 `
  --confirm-index damai-knowledge-read-v-20260919-001
```

工具先创建带 `dynamic=strict` Mapping 的具体索引，再用单个 NDJSON Bulk 写入，等待可搜索后核对文档总数，最后通过一个 `_aliases` 请求切换读取别名。移除旧别名时设置 `must_exist=true`，任一动作失败则整组失败；任一步失败都不会继续后续步骤，失败响应正文不会进入异常消息。

5. 使用运行时只读 Key 执行 `tests/test_elasticsearch_live_acceptance.py`，再执行业务 Eval。通过后保留发布回执、审批记录、内容版本、具体索引名和验收结果。

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
