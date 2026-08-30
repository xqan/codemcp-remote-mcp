# codemcp-remote-bridge

本包是 codemcp-remote 的本机 Bridge。当前已实现 loopback-only MCP
Server、项目注册、路径/命令策略、结构化错误、固定版 codemcp Adapter、SQLite
生命周期持久化、Bridge-owned Git checkpoint、受限 diff 和 compare-and-swap
rollback。Secure MCP Tunnel 的启动、profile 校验和诊断脚本位于仓库根目录
`scripts/`，Bridge 本身仍只监听 loopback。

Phase 1 已确定 codemcp mutation worker 运行在 WSL2；原生 Windows 的
Git-backed mutation 不支持。Adapter 必须显式处理 Windows/WSL 路径映射、
worker 超时和进程树清理，详见
`docs/reports/compatibility/codemcp-compatibility-matrix.md`。

本包不包含任何模型调用。

本地检查和启动：

~~~text
uv run --project bridge codemcp-bridge-server check
uv run --project bridge codemcp-bridge-server serve
~~~
