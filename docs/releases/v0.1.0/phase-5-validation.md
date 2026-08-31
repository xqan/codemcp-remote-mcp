# Phase 5 Validation

Date: 2026-08-22

## Implemented scope

Phase 5 adds the Windows local wrapper around OpenAI Secure MCP Tunnel:

- `scripts/start-bridge.ps1` starts the existing loopback Bridge;
- `scripts/start-tunnel.ps1` creates or runs a named HTTP MCP profile;
- `scripts/doctor.ps1` reports Bridge configuration, Bridge health, profile
  validity, tunnel-client doctor output, and tunnel `/healthz`/`/readyz`;
- `config/tunnel-profile.example.env` documents non-secret runtime settings;
- `docs/guides/tunnel-setup.md` documents setup and operator recovery;
- `tests/e2e/test_tunnel_contract.md` defines the account-backed ChatGPT
  developer-mode acceptance contract.

The wrapper enforces the following local boundary before starting the client:

- OpenAI control plane only (`api.openai.com` or `mtls.api.openai.com`);
- `env:CONTROL_PLANE_API_KEY` instead of a plaintext profile key;
- exactly one HTTP MCP target at `http://127.0.0.1:46200/mcp`;
- no stdio target and no remote health/admin bind.

## Local validation performed

The installed `tunnel-client` reported version `0.0.11+8d55683eeef80bc5e360d95abf4692454fafc615`.
Its `help quickstart`, `init --help`, `doctor --help`, and `run --help` output
were checked against the wrapper flags and environment variable names.

The following checks passed:

```text
PowerShell parser: 4 scripts passed
tunnel-client init: generated an HTTP profile for the loopback Bridge
profile contract validation: passed
non-loopback MCP URL rejection: passed
non-loopback health/admin bind rejection: passed
example profile with placeholder tunnel_id: rejected as expected
doctor.ps1 -SkipTunnel: emitted structured diagnostics and correctly reported
  Bridge health as unavailable when Bridge was not running
```

The existing Bridge and codemcp test suites were run as the required
regression checks: the Bridge suite passed `25 passed, 1 skipped, 2 xfailed`,
and the fixed codemcp compatibility suite passed `4 passed, 2 xfailed`.

## Account-backed validation

On 2026-08-22, the real Tunnel and ChatGPT workspace completed the main
contract flow against a disposable registered Git project:

- Tunnel-connected Bridge tools were discovered;
- `project_open`, `project_status`, `file_read`, `code_search`, and
  `file_list` succeeded consecutively in one session;
- deterministic `file_edit`, registered `test_run`, `git_diff`, and
  `operation_status` succeeded;
- explicit approval, manual checkpoint creation, compare-and-swap rollback,
  rollback safety checkpointing, and idempotent mutation replay succeeded;
- the final project returned to its original HEAD with a clean worktree.

The connector reused `request_id="0"` for multiple read calls. Bridge now
keeps that external correlation value but generates a unique internal read
operation key, so distinct read calls do not collide in the idempotency table.
The regression is covered by
`test_static_zero_request_id_does_not_conflict_for_read_operations`.

The failure and recovery matrix was also completed on 2026-08-22:

- stopping Tunnel caused remote MCP failure without a mutation replay;
- stopping Bridge caused an MCP internal error while Tunnel remained up;
- restarting Bridge restored `doctor.ps1`, `/healthz`, and `/readyz`; the old
  session was rejected instead of replayed, and a new session worked;
- a rollback approval raced with an external HEAD commit and returned
  `CHECKPOINT_CONFLICT` without resetting the external HEAD;
- the existing local profile-contract checks covered non-loopback targets and
  plaintext API-key rejection.

Phase 5 account-backed validation is complete. Phase 6 remains intentionally
unstarted; its operationalization work must follow the frozen Windows plan in
[Windows release baseline](../../plans/v0.1.0/windows-release-baseline-2026-08-28.md).
