# 架构与安全边界

英文 canonical：

- [Architecture](../architecture/architecture.md)
- [Security Model](../architecture/security-model.md)
- [Threat Model](../architecture/threat-model.md)
- [Git Policy](../architecture/git-policy.md)

## 1. 核心架构

```text
ChatGPT
  -> remote transport / network boundary
  -> loopback codemcp-remote Bridge
  -> policy / operation / approval / audit
  -> codemcp worker
  -> registered Git repository
```

职责严格分离：

- ChatGPT：理解请求、规划、生成参数和修改意图；
- Cloudflare / OAuth：网络或身份边界；
- Bridge：本地授权、安全策略、operation 状态、审批、审计和 Git 安全；
- codemcp worker：执行后端；
- Git：可验证 mutation 基线和恢复边界。

Bridge 不是 agent，codemcp 也不会直接暴露给 ChatGPT。

## 2. 项目授权

只有本机管理员显式注册的项目可以被 MCP 使用。

MCP 不能：

- 新增项目；
- 删除项目；
- 修改允许的根目录；
- 修改安全策略；
- 任意指定本机路径。

项目文件访问必须经过 root containment 和 sensitive-path policy。

## 3. 命令边界

远程调用不能提供任意 shell 文本或任意 argv。

可运行的命令必须提前由本地可信配置注册，通过固定 `command_id` 调用。

因此：

```text
registered command != remote shell
```

但本地管理员如果注册了危险命令，该命令本身仍然危险。本地配置属于根信任的一部分。

## 4. Mutation 模型

mutation 具备：

- `client_request_id`；
- canonical request hash；
- per-project serialization；
- restart-safe operation state；
- persisted replay result；
- audit trail。

相同身份、相同 request ID、相同 request hash 的已完成操作会 replay 已保存结果，而不是执行第二次。

同一个 request ID 绑定不同内容时 fail closed。

## 5. Git checkpoint 与 CAS

mutation 前 Bridge 记录 Git baseline 并创建 Bridge-owned checkpoint。

恢复时要求：

- checkpoint 属于当前 project/session；
- checkpoint ref 可验证；
- branch 符合策略；
- 当前 HEAD 等于 expected HEAD；
- worktree clean；
- approval 有效。

restore 前还会创建 rollback safety checkpoint。

如果外部 Git 状态已经变化，Bridge 不覆盖它，而是返回 conflict。

## 6. `unknown` 为什么重要

如果 Bridge 无法证明某个副作用到底发生还是没发生，operation 不会假装成功或失败，而会进入：

```text
unknown
```

这时禁止盲目重试 mutation，因为重试可能产生重复副作用。

正确流程：

1. 查看 `operation_status`；
2. 检查 Git / checkpoint / audit evidence；
3. 通过显式 reconciliation 把结果归类。

## 7. Approval

高风险操作使用短时、一次性 approval。

明文 approval token 不持久化到 SQLite。

Approval 只是额外门禁，不替代：

- project authorization；
- path policy；
- branch policy；
- clean worktree；
- checkpoint；
- CAS；
- audit。

## 8. Network trust 不等于身份认证

推荐个人部署中的 Cloudflare IP allowlist 只能说明：

> 请求从操作者配置允许的 OpenAI/ChatGPT Connector egress 网络到达 Cloudflare。

它不能证明：

- 具体 ChatGPT 用户；
- ChatGPT 账号；
- Workspace；
- 会话；
- 对某个本地项目的授权。

需要 subject/client/scope identity 时使用 OAuth Resource Server profile。

## 9. 主要剩余风险

Bridge 不能消除所有本地风险：

- 本机 OS 账户被攻陷；
- trusted local config 被恶意修改；
- registered command 本身危险；
- 普通文件名下存放 secret；
- 依赖或工具链被攻陷；
- Git 远端状态没有反映到本地 refs。

因此 codemcp-remote 的目标是**缩小和验证远程执行边界**，不是把本机变成无条件安全环境。
