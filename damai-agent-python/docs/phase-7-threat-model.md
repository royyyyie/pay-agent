# 阶段 7：威胁模型

## 1. 边界与资产

受保护资产包括：租户与用户身份、Delegation Token、模型和 Java 密钥、购票人引用、确认 Grant、订单状态、Prompt/响应、Tool 审计、Checkpoint、知识索引和费用数据。公网入口属于 Java BFF；Python Agent 只接受内部认证和短时委托。Java 仍是动态票务事实与交易状态的唯一来源。

信任边界依次为：客户端 → Java BFF → Python Agent → Provider/Elasticsearch/Java Tool Gateway → PostgreSQL/Redis/Kafka。任何跨边界数据都必须经过认证、Schema、Scope、风险上限、租户绑定和输出脱敏。

## 2. STRIDE 风险登记

| 类别 | 主要场景 | 已有控制 | 生产前证据 |
|---|---|---|---|
| Spoofing | 伪造 BFF、委托或内部请求 | 强密钥、HMAC、短 TTL、常量时间比较、租户/Session/Turn 绑定 | 密钥轮换、重放与过期 Token 测试 |
| Tampering | 修改 Tool 参数、Checkpoint、订单事实 | OpenAPI 单源、严格 Schema、DB fencing、稳定订单号、查询优先恢复 | DB 权限审计、篡改与并发故障注入 |
| Repudiation | 否认确认、调用或订单提交 | 持久化 Tool 审计、Trace、一次性 Grant、命令状态机 | 审计保留期、时钟、检索与审批抽查 |
| Information disclosure | Prompt、PII、Secret 出现在日志/Trace/输出 | 引用型购票人、Payload-free Metrics、错误清洗、Secret 外置 | 日志/Trace/崩溃转储扫描 |
| Denial of service | 长 SSE、昂贵模型、Tool 风暴、连接耗尽 | Token/费用预算、并发上限、超时、排空、资源限制 | 峰值与长稳压测、限流和熔断报告 |
| Elevation of privilege | READ_ONLY 越权为写操作或跨租户访问 | Tool allowlist、Scope、Risk Ceiling、Java 二次鉴权、功能开关 | 权限矩阵与跨租户红线 Eval |
| Supply chain | 恶意依赖、镜像漂移、CI 凭据泄漏 | 锁文件、固定基础镜像/Action SHA、最小工作流权限、PR 扫描、main-only 发布、SBOM 和来源证明 | 核验扫描结果、GHCR digest、证明与部署 digest 一致 |
| Agent-specific | Prompt Injection、RAG 污染、模型诱导下单 | 动态事实隔离、来源白名单、强制引用、模型外确认 | 对抗 Eval、知识审批、订单零未确认报告 |

## 3. 禁止项

- 禁止 Shell、任意文件系统、任意 URL 抓取和未授权第三方票务接口。
- 禁止验证码、排队、风控或平台限制绕过。
- 禁止模型生成或保存证件号码、支付数据和确认 Grant。
- 禁止对未知写入结果自动重试；只能按稳定业务键查询对账。
- 禁止直接把示例 Secret、`.env` 或完整生产配置打入镜像。

## 4. 待评审风险

第二批必须由安全、SRE、业务和交易负责人共同确认云网络、密钥托管、依赖漏洞、日志落点、备份、RTO/RPO、供应商数据保留和事件响应责任。未形成签字证据前，本文件只是工程威胁模型，不代表生产安全审批通过。

发布证据门禁会拒绝缺项、过期、未来时间、提交/digest 不匹配、故障场景不足、非零安全红线或审批早于测量窗口的声明。它验证的是结构、绑定关系和硬阈值，不能证明上传文件内容真实；原始证据仍须存放在只追加、受权限控制的系统，并由审批人核验。
