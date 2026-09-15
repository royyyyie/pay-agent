# ADR-002：公网入口由 Java BFF 承载

- 状态：Accepted
- 日期：2026-09-06

## Context

用户身份、租户、会话和业务权限已经属于 Java 平面，Python 不应建立第二套公网身份体系。

## Decision

生产环境由 Java Gateway/BFF 接收公网请求并签发短时委托身份；Python Agent API 仅在内网开放。当前 Python 直连接口只用于 local/test。

## Consequences

Python staging/production Profile 必须启用内部认证；后续以 mTLS 和 Delegation Token 取代阶段 0 的内部 API Key。
