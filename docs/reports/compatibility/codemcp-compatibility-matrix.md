# codemcp Phase 1 Compatibility Matrix

[Simplified Chinese](../../zh-CN/codemcp-compatibility-matrix.md)

## Conclusion

Pinned `codemcp==0.3.0` can run Git-backed subtools as a **native Windows 11 stdio worker**. `codemcp-remote` no longer requires the mutation worker to run inside WSL2. The default worker mode is `local`; WSL2 Ubuntu remains an explicit compatibility fallback.

The project does not maintain a `codemcp` fork. Windows-specific behavior is isolated in `codemcp_bridge.native_codemcp_worker`, which applies two narrow compatibility adaptations to pinned upstream `0.3.0`:

1. when an upstream `asyncio.create_subprocess_exec` call does not explicitly supply stdin, the wrapper provides `stdin=asyncio.subprocess.DEVNULL`, preventing Git/command child processes from inheriting the MCP stdio server input and blocking;
2. the worker-process `codemcp.tools.file_utils.write_file_sync` path is wrapped so already-normalized line endings are written with `newline=""`, preventing Windows text mode from translating `\r\n` a second time into `\r\r\n`.

The native Windows gate can be launched from a WSL test host through Windows PowerShell and Windows `uv`, so Windows behavior is exercised by the actual Windows worker rather than simulated by WSL.

The compatibility-baseline run recorded **142 passed**, with the native Windows gate PASS while retaining WSL2 fallback coverage.

## Version and validation environment

- Upstream repository: [ezyang/codemcp](https://github.com/ezyang/codemcp)
- Release/tag: `0.3.0`
- Release commit: `683e6ec29b15b91ec12430afabf5a45ed57d2489`
- Project Python requirement: `>=3.12`
- Primary host: Windows 11
- Native Git: Git for Windows
- Fallback host: WSL2 Ubuntu
- codemcp installation source: PyPI, pinned in `bridge/uv.lock`
- Windows compatibility entry point: `codemcp_bridge.native_codemcp_worker`

## MCP contract

The probe uses the MCP SDK `stdio_client` and `ClientSession`, with an isolated HOME for every worker.

| Check | Native Windows | WSL2 Ubuntu | Observed behavior |
| --- | --- | --- | --- |
| Worker startup | PASS | PASS | Native path uses the Bridge compatibility entry point; WSL2 uses the pinned Python worker |
| `initialize` | PASS | PASS | MCP session initializes |
| `tools/list` | PASS | PASS | Exposes a single `codemcp` MCP tool |
| Input/output schema | PASS | PASS | `subtool` is required |
| `ReadFile` | PASS | PASS | Unicode/CJK, spaces, and deeply nested paths are readable |
| Git-backed subtools | PASS | PASS | 30-second bounded compatibility budget |
| Worker close/restart | PASS | PASS | Worker can initialize again after context close |
| Two stdio workers in parallel | PASS | PASS | Both independent workers discover tools |
| stdio port collision | N/A | N/A | stdio workers do not listen on a port |

Observed `subtool` values include:

`InitProject`, `ReadFile`, `WriteFile`, `EditFile`, `LS`, `Grep`, and `RunCommand`.

`Format` is not a separate subtool. Formatting remains expressed as a registered `RunCommand` command from `codemcp.toml`.

## Tool and Git behavior

| Capability | Native Windows | WSL2 Ubuntu |
| --- | --- | --- |
| `InitProject` | PASS | PASS |
| `LS` / `Grep` | PASS | PASS |
| `ReadFile` | PASS | PASS |
| `EditFile` / `WriteFile` | PASS | PASS |
| `RunCommand` | PASS | PASS |
| Unicode/CJK and space-containing paths | PASS | PASS |
| Worker restart / duplicate startup | PASS | PASS |
| Bounded worker timeout policy | PASS | PASS |

Git-backed edit/write validation checks HEAD, commit count, and a clean worktree. Windows `WriteFile` additionally covers CRLF semantics so the compatibility layer cannot introduce blank-line corruption.

## Windows root causes and compatibility boundary

### 1. Git-backed subtools could block on inherited stdin

Upstream `codemcp 0.3.0` calls:

```python
await asyncio.create_subprocess_exec(
    *cmd,
    cwd=cwd,
    env=env,
    stdout=stdout_pipe,
    stderr=stderr_pipe,
)
```

No stdin is specified. Because the MCP server itself uses stdio transport, a Git/command child process can inherit the same stdin on Windows and block.

The Bridge compatibility entry point modifies only Windows worker-process calls where the caller did not explicitly provide stdin, supplying `DEVNULL`. An explicitly provided stdin is preserved.

### 2. Windows line endings could be translated twice

Upstream `write_text_content` first normalizes `\n` to the target file line ending such as `\r\n`. A later default text-mode write on Windows can translate those line endings again, producing `\r\r\n`.

The Bridge wrapper changes only the synchronous write helper inside the worker process and uses `newline=""`, preserving the line endings already normalized by codemcp. Upstream package files are not modified.

## Lifecycle and security boundary

- `worker_mode = "local"` is the default.
- `worker_mode = "wsl2"` remains supported as an explicit fallback.
- Worker timeout is enforced by the outer Bridge; it does not depend on an upstream command-internal timeout.
- Timeout/cancellation fails closed; a mutation timeout maps to `UNKNOWN_SIDE_EFFECT`.
- `stop-all.ps1` recognizes both native Windows and WSL2 fallback workers and can clean Bridge-owned/orphaned worker process trees.
- Full multi-cycle lifecycle, forced process crash, and clean-machine operational stress are release gates and are not replaced by this compatibility matrix.

## Command security boundary

Upstream `RunCommand` reads commands from `codemcp.toml` and supports appended arguments.

The Bridge does **not** expose that unrestricted argument surface to ChatGPT. Remote callers provide a registered command ID; the Bridge resolves the fixed structured argv under local policy.

## ChatGPT-only boundary

- `codemcp` remains pinned to upstream `0.3.0`.
- This compatibility work adds no model provider.
- Bridge policy remains `model_egress = "deny"`.
- The Windows compatibility wrapper changes only local subprocess and file-write behavior.

## Validation

Primary gate:

```text
uv run --project bridge pytest -q
```

The suite also includes `test_native_windows_worker_host.py`. When the test host is WSL, it launches Windows PowerShell and Windows `uv` to execute the native-worker unit tests and `test_codemcp_compatibility.py`, so a Windows PASS is based on Windows execution rather than a WSL approximation.

Recorded compatibility-baseline result: **142 passed**.

This report is historical compatibility evidence. Current product support and release claims are defined by the current architecture, operator guides, and acceptance documents.
