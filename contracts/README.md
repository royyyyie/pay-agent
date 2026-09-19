# Agent 契约治理

`agent-tools-v1.openapi.yaml` 是 Java Tool Gateway 与 Python Agent 之间的 v1
单一契约来源。Java DTO、Python Tool Schema、契约样例和测试必须与该文件保持一致。

## 版本规则

- URL 主版本固定为 `/internal/agent/v1/`。
- `info.version` 使用 SemVer。
- 新增可选字段属于兼容变更；删除、重命名、收紧约束或改变语义属于破坏性变更。
- 破坏性变更必须新建主版本文件和 URL，不直接覆盖 v1 行为。
- `x-agent-tool-name` 是模型看到的稳定 Tool 名，不等同于 Java `operationId`。
- `x-agent-risk`、`x-agent-scope` 和 `x-agent-timeout-ms` 是执行策略元数据。

## 生成与校验

在 `damai-agent-python` 目录执行：

```powershell
openapi-spec-validator ../contracts/agent-tools-v1.openapi.yaml
python scripts/generate_tool_schemas.py
python scripts/generate_tool_schemas.py --check
python scripts/check_contract_compatibility.py
```

生成文件为 `damai_agent/generated/tool_schemas.py`。禁止直接编辑生成文件；CI 会重新生成并检查差异。兼容性检查以 `baselines/agent-tools-v1.0.0.openapi.yaml` 为冻结基线，新增兼容能力时不更新该基线。

v1 内部调用必须携带 Tool Call、Turn、Session 和 W3C `traceparent` Header。Java 在进入控制器前验证上下文，Python 为每次 Java Tool 调用创建客户端 Span ID。监控等所有者相关 Tool 还必须携带从签名委托透传的 Tenant/User Header；身份字段禁止出现在模型可写的请求 Schema 中。

## 变更流程

1. 先修改 OpenAPI 和 `examples/agent-tools-v1/` 样例。
2. 运行生成器并提交生成文件。
3. 运行 Python 测试和 Java 契约测试。
4. CI 执行生成一致性和 v1 向后兼容检查。
5. 破坏性变更必须经过 ADR 和灰度迁移。
