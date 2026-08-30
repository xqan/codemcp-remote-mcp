# Phase 3 Validation

Date: 2026-08-22

Phase 3 adds recoverable and auditable lifecycle state to the local Bridge.
The Bridge remains the only MCP server exposed to a future Secure MCP Tunnel;
no model provider or model egress was added.

## Implemented scope

- SQLite schema migrations for sessions, operations, approvals, and audit events.
- Persistent session states with restart blocking and normal shutdown closure.
- Operation state machine, project-level mutation locking, idempotency keys, and request hashes.
- One-time, short-lived approval tokens bound to an operation; only token hashes are persisted.
- Restart classification for completed, pre-dispatch, failed, and unknown operations.
- Explicit cancellation and failed reconciliation for unknown mutations.
- Stateless HTTP worker cleanup that preserves database sessions across MCP requests.

## Executed validation

The following checks passed in the Windows workspace. The full test run used a
fresh elevated temporary directory so WSL2 and Windows ACL behavior were both
exercised:

~~~text
uv sync --project bridge
uv run --project bridge codemcp-bridge doctor --json
uv run --project bridge ruff check bridge/src bridge/tests
uv run --project bridge pytest -q --basetemp=.local/pytest-escalated-phase3-full/basetemp
~~~

Result:

- 22 passed
- 1 skipped: the Windows account cannot create symlinks
- 2 expected xfail: native Windows Git-backed codemcp mutation blocker
- WSL2 codemcp Bridge read, search, edit, command, status, and diff flow passed
- Phase 3 persistence/API tests covered migration idempotency, replay, hash conflict,
  mutation locking, restart recovery, approval reuse, cancellation, and audit events

## Known limitations

- Native Windows Git-backed mutation remains unsupported; WSL2 is the selected backend.
- Git checkpoint/rollback is Phase 4.
- Secure MCP Tunnel integration is Phase 5.
- Pytest and pytest-asyncio emit existing deprecation/cache warnings in this environment;
  they did not fail the checks.
