# Architecture Decision Records

ADR 记录 Damai Agent 的长期约束。已接受的决策只能通过新的 ADR 取代，不能静默修改历史记录。

| ADR | 决策 | 状态 |
|---|---|---|
| [001](001-java-python-boundary.md) | Java/Python 职责与数据归属 | Accepted |
| [002](002-public-ingress.md) | 公网入口由 Java BFF 承载 | Accepted |
| [003](003-contract-source.md) | Tool 契约单一来源 | Accepted |
| [004](004-session-consistency.md) | Session/Turn/Checkpoint 一致性 | Accepted |
| [005](005-tool-risk.md) | Tool 风险分级与受信确认 | Accepted |
| [006](006-dynamic-facts.md) | 动态票务事实禁止由 RAG/Memory 回答 | Accepted |
| [007](007-provider-resilience.md) | Provider 路由与重试语义 | Accepted |
| [008](008-observability-privacy.md) | Trace、审计与 PII | Accepted |
| [009](009-http-sse-evolution.md) | HTTP+SSE 的初始通信方案 | Accepted |
| [010](010-authorized-connectors.md) | 仅使用官方或授权票务接口 | Accepted |
| [011](011-advanced-rag-retrieval.md) | 生产级高级 RAG 检索树 | Accepted |

新 ADR 使用下一个三位编号，并包含 Context、Decision、Consequences 和 Supersedes（如适用）。
