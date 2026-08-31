# codemcp-remote 中文文档

[English documentation](../README.md)

这里是独立维护的简体中文文档入口。中文版本重点服务安装、使用、运维和理解安全边界，不机械复制每一份历史验收报告。

> **权威规则：** 英文文档是 canonical。涉及协议、安全、发布门禁、版本状态或中英文存在差异时，以对应英文文档为准。

## 推荐阅读顺序

1. [项目中文总览](../../README.zh-CN.md)
2. [快速开始](getting-started.md)
3. [架构与安全边界](architecture-and-security.md)
4. [运维与恢复](operations.md)
5. [macOS 当前状态与使用限制](macos.md)
6. [macOS 双架构实施计划](implementation-plan.md)
7. [开源发布准备计划](open-source-readiness-plan.md)
8. [codemcp 兼容性矩阵](codemcp-compatibility-matrix.md)

## 当前支持边界

### Windows

Windows `v0.1.0` 产品/运行时 release baseline 已闭环：

- packaged `codemcp-remote.exe`；
- Native Windows worker；
- Git for Windows 为运行时前置；
- 推荐 Cloudflare Tunnel + ChatGPT network trust；
- 发布物有意 `NotSigned`，SmartScreen/reputation 限制需要明确接受。

### macOS

macOS 仍是增量发布轨道：

- GitHub-hosted native `arm64` candidate：PASS；
- GitHub-hosted native `x86_64` candidate：PASS；
- Phase 4 real clean-host：未完成；
- ad-hoc signing；
- no Developer ID；
- no notarization；
- 在 Phase 4 完成前，不应声明 macOS 已正式支持。

## 中文文档结构

| 文档 | 用途 |
| --- | --- |
| [`getting-started.md`](getting-started.md) | Windows 安装、初始化、项目注册、ChatGPT 连接 |
| [`architecture-and-security.md`](architecture-and-security.md) | 架构、信任边界、mutation、checkpoint、CAS、风险 |
| [`operations.md`](operations.md) | start/status/stop、doctor、项目管理、故障恢复 |
| [`macos.md`](macos.md) | macOS candidate、Gatekeeper、Keychain、clean-host 状态 |
| [`implementation-plan.md`](implementation-plan.md) | macOS 双架构详细实施计划（中文） |
| [`open-source-readiness-plan.md`](open-source-readiness-plan.md) | v0.1.0 开源发布准备、Gate 与历史状态（中文） |
| [`codemcp-compatibility-matrix.md`](codemcp-compatibility-matrix.md) | 固定版 codemcp 的 Windows/WSL2 兼容性证据（中文） |

## 英文 canonical 文档

### 架构与安全

- [Architecture baseline](../architecture/architecture.md)
- [Security model](../architecture/security-model.md)
- [Threat model](../architecture/threat-model.md)
- [Git policy](../architecture/git-policy.md)

### 使用与运维

- [Windows build/install/use](../guides/windows-build-install-use.md)
- [Operations runbook](../guides/operations-runbook.md)
- [Cloudflare Tunnel + ChatGPT network trust](../guides/cloudflare-tunnel-setup.md)
- [OpenAI Secure MCP Tunnel compatibility setup](../guides/tunnel-setup.md)
- [External OAuth Resource Server setup](../guides/external-mcp-auth-setup.md)
- [macOS build/install/validation](../guides/macos-build-install-use.md)

### 验收

- [Phase 6 Windows validation](../acceptance/phase-6-validation.md)
- [Phase 7 / v0.1.0 final release gate](../acceptance/acceptance-test-plan.md)
- [macOS v0.1.0 validation ledger](../acceptance/macos-v0.1.0-validation.md)

### 历史证据

`docs/releases/` 和 `docs/reports/` 默认保留英文历史证据，不做机械中文镜像。高频使用、容易误解的历史材料可以提供独立中文版本，例如本目录中的 codemcp 兼容性矩阵；这些中文版本仍不能替代当前 `architecture/`、`guides/` 和 `acceptance/` 的英文 canonical 文档。

## 维护规则

1. 默认入口与 canonical 文档统一使用英文。
2. 简体中文只放在 `README.zh-CN.md`、`bridge/README.zh-CN.md` 和 `docs/zh-CN/`。
3. 中文文档优先保证可用性和准确性，不要求与历史英文文件一一对应。
4. 英文安全/发布状态发生变化时，应同步更新相关中文使用文档。
5. 中文版本不得扩大英文文档没有承诺的支持范围。
