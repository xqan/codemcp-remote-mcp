# codemcp-remote

[English](README.md)

`codemcp-remote` 是一个受策略约束的本地 MCP Bridge，让 **ChatGPT 作为唯一推理引擎**，同时安全地操作明确注册的本地 Git 项目。

> 中文文档独立维护。涉及安全边界、发布门禁或中英文冲突时，以英文 canonical 文档为准。

进入 [中文文档中心](docs/zh-CN/README.md)。

## 核心边界

- 只有本机明确注册的项目可以访问；
- 路径必须位于注册项目根目录内，敏感路径默认拒绝；
- 只允许预注册 `command_id`，不开放任意 shell/argv；
- mutation 按项目串行，并使用 request ID + canonical request hash 保证幂等；
- 高风险操作使用一次性 approval；
- mutation 由 Bridge-owned Git checkpoint、branch/HEAD CAS 和 audit 保护；
- 无法确认副作用时 operation 保持 `unknown`，必须显式 reconcile；
- Bridge 只监听 loopback；
- Bridge 没有模型提供方或 agent loop，仓库内容不能自行授权高权限操作。

## 当前支持

**Windows `v0.1.0`**：产品/运行时验收基线已闭环。发布物有意 `NotSigned`，SmartScreen/reputation 警告属于已接受限制。安装版使用 Native Windows worker，Git for Windows 是运行时前置，不要求 Python、`uv`、PowerShell 7 或 WSL2。

**macOS**：native `arm64` / `x86_64` candidate gate 已 PASS，但 Phase 4 real clean-host 仍未完成，因此当前不能声明正式支持。发布策略为 ad-hoc signing、无 Developer ID、无 notarization；必须先验证 SHA-256，再由用户显式处理 quarantine。

详见 [macOS 中文说明](docs/zh-CN/macos.md)。

## 推荐部署

```text
ChatGPT Connector
  -> OpenAI / ChatGPT Connector egress
  -> Cloudflare WAF/IP allowlist
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200/mcp
  -> codemcp-remote Bridge
  -> registered local Git project
```

Cloudflare IP allowlist 是 network trust/provenance boundary，不是具体用户、账号、Workspace 或会话身份认证。需要 subject/client/scope 身份时使用可选 OAuth Resource Server profile。

## Windows 快速开始

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"
$env:TUNNEL_TOKEN = "<从本机 secret manager 读取>"

& $exe init `
  --transport cloudflare `
  --public-url "https://mcp.example.com/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host mcp.example.com `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret

$env:TUNNEL_TOKEN = $null

& $exe project add my_project "D:\workspace\my-project"
& $exe doctor
& $exe start
& $exe status
```

Tunnel token 使用 Windows DPAPI 保存。不要把真实 token 放入聊天、仓库、Shell 历史或日志。

## 中文入口

- [快速开始](docs/zh-CN/getting-started.md)
- [架构与安全](docs/zh-CN/architecture-and-security.md)
- [运维与恢复](docs/zh-CN/operations.md)
- [macOS 状态](docs/zh-CN/macos.md)
- [中文文档中心](docs/zh-CN/README.md)

## License

项目使用 **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`)。详见 [LICENSE](LICENSE)。
