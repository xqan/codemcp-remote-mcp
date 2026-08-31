# 运维与恢复

[English canonical runbook](../guides/operations-runbook.md)

## 常用命令

已安装 Windows 版本：

```powershell
& $exe doctor
& $exe start
& $exe status
& $exe stop
```

优先使用正式 CLI 管理生命周期，不要把 source-development helper 当成已安装产品契约。

## 项目管理

新增项目：

```powershell
& $exe project add my_project "D:\workspace\my-project"
```

项目 add/remove 属于本机管理控制面。MCP 不提供远程项目授权管理能力。

注册表经过验证后，运行中的 Bridge 会自动观察正常变更。

## 启动前检查

至少确认：

- 当前配置有效；
- Git 可用；
- 目标项目是真实 Git worktree；
- 目标 branch 在 allowlist；
- worktree clean；
- Cloudflare Tunnel 配置指向 loopback Bridge；
- secret 来自正确的安全存储。

## Mutation 前检查

第一次修改前建议执行：

```text
project_open
project_status
git_status
```

确认：

- project ID 正确；
- branch 正确；
- HEAD 是预期 commit；
- dirty=false。

## Operation 状态

mutation 不是“一次 HTTP 调用成功就算完成”。

Bridge 持久化 operation 状态，并用 request ID/hash 防止重复副作用。

如果收到：

```text
unknown
```

不要直接换一个 request ID 重试。先查看原 operation，再根据 Git 和 audit evidence reconcile。

## Checkpoint restore

restore 是高风险操作，需要：

- 当前 session；
- checkpoint ID；
- expected current HEAD；
- clean worktree；
- branch policy；
- approval。

Bridge 在真正 rollback 前建立 safety checkpoint，并使用 compare-and-swap 拒绝外部 Git 竞态。

## Tunnel 或 Bridge 异常

Tunnel 异常不应该改变本地 Git 状态。恢复 Tunnel 后，用同一 operation identity 查询已有 operation，而不是重新制造 mutation。

Bridge 异常重启后，旧 session 可能失效。未能证明结果的 mutation 应保持 `unknown`，由 successor session 根据证据 reconcile。

## Secret 处理

Windows：

```text
DPAPI
```

macOS candidate：

```text
Keychain
```

禁止把 secret 放入：

- Git；
- README 示例的真实值；
- shell history；
- argv；
- log；
- runtime TOML；
- release artifact。

## 故障排查顺序

1. `doctor`
2. `status`
3. `project_status`
4. `git_status`
5. `operation_status`
6. 对照 checkpoint/audit
7. 必要时 stop/start
8. 只有在证据明确时才 reconcile 或 restore

## 进一步阅读

- [Architecture and security](architecture-and-security.md)
- [Windows canonical guide](../guides/windows-build-install-use.md)
- [Cloudflare canonical guide](../guides/cloudflare-tunnel-setup.md)
- [Git policy](../architecture/git-policy.md)
