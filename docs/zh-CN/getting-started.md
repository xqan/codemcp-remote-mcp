# 快速开始

[English canonical guide](../guides/windows-build-install-use.md)

本文面向 Windows `v0.1.0` 推荐部署路径。

## 1. 前置条件

已安装发布版本只要求：

- Windows 11 x64-compatible；
- Git for Windows；
- 你自己的 Cloudflare Tunnel；
- 用于 ChatGPT Connector 出站范围的 Cloudflare WAF/IP List；
- ChatGPT Connector 能力。

已安装运行时不要求 Python、`uv`、PowerShell 7 或 WSL2。

## 2. 推荐安全拓扑

```text
ChatGPT Connector
  Authentication = No authentication
        |
        v
OpenAI / ChatGPT Connector egress
        |
        v
Cloudflare Edge/WAF IP allowlist
        |
        v
Cloudflare Tunnel
        |
        v
127.0.0.1:46200/mcp
        |
        v
codemcp-remote Bridge
        |
        v
已注册本地 Git 项目
```

Cloudflare IP allowlist 只能证明网络来源落在你允许的 ChatGPT Connector 出站范围内，不能证明具体用户身份。

## 3. 初始化

假设安装后的 executable 是：

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"
```

只在当前 PowerShell 进程中加载 Tunnel token：

```powershell
$env:TUNNEL_TOKEN = "<从本机 secret manager 读取>"
```

初始化：

```powershell
& $exe init `
  --transport cloudflare `
  --public-url "https://mcp.example.com/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host mcp.example.com `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret
```

随后立即清掉进程环境变量：

```powershell
$env:TUNNEL_TOKEN = $null
```

Windows 使用 DPAPI 保存 Tunnel token。不要把 token 放入聊天、仓库、配置模板、Shell 历史或日志。

## 4. 注册项目

项目注册属于本机管理操作，MCP 客户端不能远程新增或删除授权项目。

```powershell
& $exe project add my_project "D:\workspace\my-project"
```

运行中的 Bridge 会观察经过验证的项目注册表变化，不需要为了正常 add/remove 重启 Bridge、Tunnel 或 ChatGPT Connector。

只注册你明确希望 ChatGPT 访问的 Git 仓库。

## 5. 诊断和启动

```powershell
& $exe doctor
& $exe start
& $exe status
```

`doctor` 应确认：

- Bridge 配置有效；
- Cloudflare transport 可用；
- network trust 已就绪；
- exact allowed host 正确；
- `identity_level = network-only`；
- Tunnel secret 来自 DPAPI；
- Git/项目运行条件满足。

停止：

```powershell
& $exe stop
```

## 6. ChatGPT Connector

Connector 使用：

```text
Authentication = No authentication
URL = https://你的域名/mcp
```

同时必须在 Cloudflare Edge/WAF 上保护对应 hostname，只允许你维护的 ChatGPT Connector egress IP List。

Bridge 仍必须只监听：

```text
127.0.0.1:46200
```

不要把 Bridge 改为 `0.0.0.0`。

## 7. 第一次远程请求

建议先只读：

1. `project_open`
2. `project_status`
3. `file_read`

确认 branch、HEAD 和 worktree 状态正确后，再进入 mutation。

默认策略会拒绝不允许的分支和 dirty worktree。

## 8. Source development

源码开发需要：

```powershell
uv sync --project bridge
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests tests/integration
uv run --project bridge pytest -q bridge/tests tests/integration
```

完整英文说明：

- [Windows build/install/use](../guides/windows-build-install-use.md)
- [Cloudflare setup](../guides/cloudflare-tunnel-setup.md)
- [Operations runbook](../guides/operations-runbook.md)
