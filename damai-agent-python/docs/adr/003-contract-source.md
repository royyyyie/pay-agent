# ADR-003：Tool 契约单一来源

- 状态：Accepted
- 日期：2026-09-06

## Context

Java OpenAPI 与 Python `ToolSpec` 双写会导致参数、说明和版本漂移。

## Decision

`contracts/agent-tools-v1.openapi.yaml` 是 v1 Tool 契约唯一来源。Python Tool Schema 由生成器产生；风险、Scope、超时和模型 Tool 名通过 `x-agent-*` 扩展记录。

## Consequences

生成文件不可手工编辑，CI 必须执行 `--check`。破坏性变更新建主版本，不覆盖 v1 语义。
