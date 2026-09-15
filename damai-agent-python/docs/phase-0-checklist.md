# 阶段 0：契约与工程基线

开始日期：2026-09-06

## 已落地

- [x] 企业级总体开发文档
- [x] ADR-001～ADR-010
- [x] Python 3.11 项目与 CI 基线
- [x] local/test/staging/production 配置分层
- [x] staging/production 弱配置启动拒绝
- [x] staging/production Python API 临时内部密钥保护
- [x] OpenAPI v1 单一来源与 `x-agent-*` 执行元数据
- [x] Python Tool Schema 自动生成与漂移检查
- [x] 契约请求/响应样例
- [x] Ruff、Mypy、Pytest、Coverage CI 工作流
- [x] Python 3.11 可复现 `uv.lock`
- [x] Java 共享样例契约测试
- [x] v1 Tool 契约 Breaking Change 检查
- [x] W3C `traceparent` Python/Java 传播与 Java MDC 清理

## 本轮本地验收

- [x] OpenAPI v1 契约校验通过
- [x] Tool Schema 生成文件无漂移
- [x] Ruff 格式与规则检查通过
- [x] Mypy 严格模式通过（11 个源码文件）
- [x] Pytest 在 Python 3.11.16 下通过（17 项），分支覆盖率 72.66%（门槛 70%）
- [x] Java Maven Reactor 构建通过（目标 Java 17，本地 JDK 18，30 个模块），Agent 契约与 Trace 测试 6 项通过

本地已通过 uv 隔离安装 Python 3.11.16 完成验收；权威发布门禁仍以首次 GitHub Actions 绿色构建为准。

## 阶段 0 剩余验证

- [ ] 在 GitHub Actions 的 Python 3.11 环境完成首次绿色构建
- [ ] 在本地或测试环境完成 Java/Python 健康检查与 Trace 贯通演示

## 退出标准

阶段 0 完成需要 CI 全绿、生成文件无漂移、生产弱配置无法启动，并形成 Java/Python 契约测试闭环。完成后进入阶段 1：只读运行时内核。
