# ADR-015：生产生命周期、探针与排空语义

## Context

Agent 同时承载普通 HTTP、长连接 SSE 和可能进入 Tool 阶段的 Turn。容器收到终止信号后，如果继续接收新请求，滚动升级会放大断流、重复恢复和未知写入风险；如果存活探针直接依赖 Provider、Redis 或 PostgreSQL，短暂下游故障又会触发无意义的重启风暴。

## Decision

1. `/livez` 只证明进程事件循环可服务，不探测外部依赖，也不暴露配置。
2. `/readyz` 同时检查生命周期和启用中的关键依赖；排空开始后立即返回 503。
3. 只对 `/api/` 业务入口执行准入。请求一旦准入，会一直计数到完整 ASGI 响应结束，因此 SSE 不会在返回响应头后被误认为完成。
4. 终止时先进入 `DRAINING`，拒绝新业务请求，再在有界时间内等待在途请求；随后关闭 RAG、Redis 和 Trace Provider。超时会留下指标，不延长到无限等待。
5. Uvicorn 的 graceful timeout 与应用排空超时使用同一配置；Kubernetes `terminationGracePeriodSeconds` 必须更长，并先停止路由再发送终止信号。
6. 保留 `/health`、`/ready` 兼容入口，新部署只使用 `/livez`、`/readyz`。

## Consequences

- 滚动发布不会接收无限新增 Turn；SSE 和普通请求共享一致的在途语义。
- 下游短暂故障只会摘除流量，不会因 liveness 失败重启进程。
- 排空超时后仍可能由平台终止进程，durable runtime 必须依靠 Checkpoint、租约和查询优先恢复保证正确性。
- 单实例内存模式只能用于本地开发；生产灰度必须使用多副本 durable runtime。

## Supersedes

无。
