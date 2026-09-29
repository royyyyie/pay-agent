# ADR-016：供应链与发布证据门禁

## Context

单元测试、容器构建成功和人工勾选 Checklist 都不能证明某个生产候选版本已通过真实 Staging 容量、故障、回滚、安全和审批验收。可变基础镜像或 GitHub Action 标签也会使同一提交在不同时间得到不同产物。

## Decision

1. Docker 基础镜像固定到 SHA-256 digest，仓库使用的 GitHub Action 固定到完整提交 SHA；Dependabot 通过显式 PR 更新。
2. PR 和阶段分支构建本地镜像，执行源码/依赖/Secret/IaC/镜像扫描并生成 SPDX JSON SBOM。只有 `main` push 可获得 `packages: write`、`id-token: write` 和 `attestations: write`，发布 GHCR 镜像并生成来源证明。
3. 发布候选由 `damai.phase7.release/v1` Manifest 描述，必须绑定完整 Git 提交、镜像 digest、配置 SHA-256、Staging 时间窗、SLO、Trace、供应链结果、七类故障、四类回滚、安全红线、阶段 3～6 证据和四方审批。
4. 每份外部证据按原始字节计算 SHA-256。生成器默认拒绝缺项或阈值失败，验证器再次检查声明、时效、提交和部署 digest；固定灰度序列为 1%、5%、25%、50%、100%。
5. 自动化声明不能替代外部事实真实性和职责审批。真实文件保留、审批人身份、云网络和环境保护规则仍由组织控制。

## Consequences

- 仓库可以稳定地证明“构建了什么”和“声明绑定了哪些证据”，并阻止拿其他提交、镜像或旧报告放量。
- 分支构建不会拥有镜像写权限；发布权限只在 `main` 的独立 Job 中出现。
- 外部验收未完成时，示例 Manifest 必然失败，阶段 7 只能标记为代码工程完成，不能标记为生产准入完成。
- GitHub 分支保护、Environment 审批、云凭据和只追加证据存储仍需仓库管理员配置。

## Supersedes

无。
