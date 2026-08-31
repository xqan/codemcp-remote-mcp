# codemcp-remote Bridge

[English](README.md)

`codemcp-remote-bridge` 是 codemcp-remote 的本地策略与执行边界。它只监听 loopback，并通过受限 MCP 工具面操作明确注册的本地 Git 项目。

主要能力：

- 项目注册和路径 containment；
- 敏感路径默认拒绝；
- registered command，而不是任意 shell/argv；
- operation 状态、幂等和 replay；
- 高风险操作的一次性 approval；
- Bridge-owned Git checkpoint、bounded diff、CAS restore；
- audit 与 restart-safe `unknown` 处理；
- 本地 worker 生命周期管理；
- Cloudflare network trust 与可选 OAuth Resource Server 边界。

Bridge **不包含模型提供方，也没有独立 agent loop**。ChatGPT 是推理引擎；仓库内容属于不可信数据，不能自行授权高权限操作。

## Worker 基线

Windows packaged baseline 默认使用 **Native Windows worker**。WSL2 仅作为 source-mode compatibility fallback。

macOS 原生 `arm64` / `x86_64` candidate 构建门禁已经通过，但真实 clean-host 验收尚未闭环，因此目前不能把 macOS 描述为正式 supported。

相关文档：

- [中文文档中心](../docs/zh-CN/README.md)
- [Architecture](../docs/architecture/architecture.md)
- [Security Model](../docs/architecture/security-model.md)
- [codemcp baseline](../docs/guides/codemcp-baseline.md)

## 本地开发

```text
uv sync --project bridge
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests tests/integration
uv run --project bridge pytest -q bridge/tests tests/integration
```
