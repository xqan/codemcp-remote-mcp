# Phase 2 Validation

Date: 2026-08-22

## Scope

Phase 2 implements the local Bridge and the codemcp Adapter without Secure MCP
Tunnel or any model provider. The Bridge listens only on
`127.0.0.1:46200/mcp`; `/healthz` is available on the same loopback app.

The controlled MCP surface is:

- `project_open`, `project_status`
- `file_read`, `code_search`, `file_list`, `file_edit`
- `format_run`, `test_run`
- `git_status`, `git_diff`

The Adapter starts one serialized codemcp stdio worker per registered project.
On Windows the worker runs `codemcp==0.3.0` in WSL2 Ubuntu and maps registered
Windows paths to `/mnt/<drive>/...`. On non-Windows development systems the
same Adapter uses the local Python interpreter.

## Security checks

- Only registered `project_id` values are accepted.
- Paths must be project-relative; absolute paths, traversal, symlink/reparse
  components, sensitive names, binary files, and oversized files are rejected.
- Format and test calls accept only a registered command ID. The command's
  argv must exactly match the project's `codemcp.toml`; caller-supplied command
  arguments are not accepted.
- Mutations require an allowed Git branch and a clean workspace.
- Tool results use request/session/operation IDs and bounded text/diff output.
- codemcp startup/read failures return `BACKEND_UNAVAILABLE`; mutation timeout
  returns `UNKNOWN_SIDE_EFFECT` and the worker is discarded.
- Configuration rejects arbitrary paths, arbitrary commands, and model calls.

## Validation commands

From the repository root:

~~~text
uv sync --project bridge
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests
uv run --project bridge pytest -q --basetemp=.local/pytest-phase2-unit bridge/tests/test_phase0_config.py bridge/tests/test_phase2_policy.py bridge/tests/test_phase2_server.py bridge/tests/test_phase2_settings.py bridge/tests/test_phase2_worker.py
uv run --project bridge pytest -q --basetemp=.local/pytest-phase2-integration bridge/tests/test_phase2_integration.py
~~~

The local MCP client integration test exercises initialize/tool discovery,
read, search, list, binary/size rejection, edit, format, test, Git status and
diff against a Git project with spaces and Chinese characters in its path.
The Windows run uses WSL2 Ubuntu and the pinned `.local/bridge-venv-wsl`
environment.

The symlink policy test is skipped when the Windows account cannot create a
symlink. The registry still rejects symlink and reparse-point components by
inspection; a Windows CI account with Developer Mode or the required privilege
should run that test rather than relying on the skip.

## Deliberate limits

Phase 2 does not create SQLite files and does not implement persistent session,
operation, approval, audit, idempotency, checkpoint, rollback, or Tunnel
behavior. Native Windows Git-backed codemcp mutation remains unsupported per
the Phase 1 compatibility decision.
