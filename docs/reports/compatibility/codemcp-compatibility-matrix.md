# codemcp Phase 1 兼容性矩阵

## 结论

固定的 `codemcp==0.3.0` 现在可以作为 **Windows 11 原生 stdio worker**
运行 Git-backed 子工具。`codemcp-remote` 不再要求把 mutation worker 放进
WSL2；默认模式改为 `local`，WSL2 Ubuntu 仅保留为显式 fallback。

本项目没有维护 `codemcp` fork。Windows 差异被隔离在
`codemcp_bridge.native_codemcp_worker` 中，对固定的上游 `0.3.0` 应用两个
窄兼容修复：

1. 对没有显式 stdin 的 `asyncio.create_subprocess_exec` 调用补
   `stdin=asyncio.subprocess.DEVNULL`，防止 Git/命令子进程继承 MCP stdio
   server 的 stdin 后阻塞。
2. 替换 worker 进程内的 `codemcp.tools.file_utils.write_file_sync`，以
   `newline=""` 写入已经由 codemcp 规范化的行尾，避免 Windows 文本模式对
   `\r\n` 再做一次翻译而产生 `\r\r\n`。

真实 Windows gate 会从 WSL 测试宿主调用 Windows PowerShell 和 Windows `uv`，
执行原生 Windows 的 worker/兼容矩阵。当前完整回归结果为 **142 passed**，
原生 Windows gate PASS；同一套回归同时保留 WSL2 fallback 覆盖。

## 版本和验证环境

- 上游仓库：[ezyang/codemcp](https://github.com/ezyang/codemcp)
- Release/tag：`0.3.0`
- Release commit：`683e6ec29b15b91ec12430afabf5a45ed57d2489`
- 项目 Python 要求：`>=3.12`
- Host：Windows 11
- Native Git：Git for Windows
- Fallback：WSL2 Ubuntu
- codemcp 安装来源：PyPI，版本固定在 `bridge/uv.lock`
- Windows 兼容入口：`codemcp_bridge.native_codemcp_worker`

## MCP 合约

探针使用 MCP SDK 的 `stdio_client` 和 `ClientSession`，并为每个 worker 使用
隔离 HOME。当前结果：

| 检查项 | Windows 原生 | WSL2 Ubuntu | 实际观察 |
|---|---|---|---|
| worker 启动 | PASS | PASS | 原生使用 Bridge compatibility entry point；WSL2 使用固定 Python worker |
| `initialize` | PASS | PASS | MCP session 可初始化 |
| `tools/list` | PASS | PASS | 暴露单一 `codemcp` MCP tool |
| 输入/输出 schema | PASS | PASS | `subtool` required |
| `ReadFile` | PASS | PASS | 中文、空格和长嵌套路径可读 |
| Git-backed subtools | PASS | PASS | 30 秒 bounded compatibility budget |
| worker 关闭/重启 | PASS | PASS | context 关闭后可重新 initialize |
| 两个 stdio worker 并行启动 | PASS | PASS | 两个独立 worker 均可发现工具 |
| stdio 端口冲突 | N/A | N/A | stdio worker 不监听端口 |

实际暴露的 `subtool` 包括：

`InitProject`、`ReadFile`、`WriteFile`、`EditFile`、`LS`、`Grep`、`RunCommand`。

`Format` 不是独立 subtool；格式化仍通过登记在 `codemcp.toml` 的
`RunCommand` 命令表达。

## 工具和 Git 行为

| 能力 | Windows 原生 | WSL2 Ubuntu |
|---|---|---|
| `InitProject` | PASS | PASS |
| `LS` / `Grep` | PASS | PASS |
| `ReadFile` | PASS | PASS |
| `EditFile` / `WriteFile` | PASS | PASS |
| `RunCommand` | PASS | PASS |
| 中文/空格路径 | PASS | PASS |
| worker restart / duplicate startup | PASS | PASS |
| bounded worker timeout policy | PASS | PASS |

Git-backed edit/write 验证同时检查 HEAD、commit count 和 clean worktree。
Windows `WriteFile` 还覆盖了 CRLF 语义，防止兼容层引入额外空行。

## Windows 根因与修复边界

### 1. Git-backed 子工具阻塞

上游 `codemcp 0.3.0` 的 `codemcp/shell.py` 调用：

~~~python
await asyncio.create_subprocess_exec(
    *cmd,
    cwd=cwd,
    env=env,
    stdout=stdout_pipe,
    stderr=stderr_pipe,
)
~~~

没有为子进程指定 stdin。MCP server 自己使用 stdio transport，因此 Windows
Git/命令子进程继承同一 stdin 时可能阻塞。

Bridge compatibility entry point 只在 Windows worker 进程中为“调用方没有
显式传 stdin”的 subprocess 补 `DEVNULL`。显式 stdin 仍被保留。

### 2. Windows 行尾二次翻译

上游 `write_text_content` 会先根据目标文件把 `\n` 转为所需的 `\r\n`，
随后 `write_file_sync` 使用默认文本模式再次写入。在 Windows 上这会再次翻译
换行，产生 `\r\r\n`。

Bridge wrapper 只替换 worker 进程内的同步写入 helper，并使用 `newline=""`，
让已经规范化的行尾按原样写入。上游包文件本身没有被修改。

## 生命周期和安全边界

- `worker_mode = "local"` 是默认值。
- `worker_mode = "wsl2"` 仍受支持，可作为 fallback。
- worker timeout 继续由 Bridge 外层强制执行；不能依赖上游命令的内部 timeout。
- timeout/cancellation 会 fail closed；mutation timeout 映射为
  `UNKNOWN_SIDE_EFFECT`。
- `stop-all.ps1` 同时识别 native Windows worker 和 WSL2 fallback worker，
  可清理 Bridge-owned/orphaned worker process tree。
- 完整的多轮 start/stop、强制进程崩溃和 clean-machine operations stress
  仍属于发布生命周期 gate，不由本兼容矩阵单独替代。

## 命令安全边界

上游 `RunCommand` 从 `codemcp.toml` 读取命令列表，并允许追加 arguments。
Bridge 不直接暴露这个参数面给 ChatGPT，只接受登记的 command ID 和 Bridge
解析出的固定结构化 argv。

## ChatGPT-only 检查

- `codemcp` 仍固定为上游 `0.3.0`；本阶段没有引入新的模型 provider。
- Bridge 保持 `model_egress = "deny"`。
- Windows compatibility wrapper 只改变本地 subprocess/file-write 行为。

## 验证

主要 gate：

~~~text
uv run --project bridge pytest -q
~~~

测试套件还包含 `test_native_windows_worker_host.py`：当测试宿主位于 WSL 时，
它通过 Windows PowerShell 调起 Windows `uv`，重新执行 native worker 单元测试
和 `test_codemcp_compatibility.py`，确保“Windows PASS”不是由 WSL 环境模拟得出。

当前结果：**142 passed**。
