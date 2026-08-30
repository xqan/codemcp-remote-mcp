# Secure MCP Tunnel contract

This is an account-backed acceptance test for Phase 5. It must be run on a
Windows 11 host with a real OpenAI `tunnel_id`, a runtime control-plane key,
and ChatGPT developer-mode access. It is intentionally a contract document,
not an automated test that could store or replay credentials.

## Preconditions

- `config/tunnel-profile.local.env` exists and contains a real
  `CONTROL_PLANE_TUNNEL_ID`.
- `CONTROL_PLANE_API_KEY` is injected into the current process by a secret
  store or equivalent runtime mechanism.
- The tunnel is associated with the target Platform organization and ChatGPT
  workspace.
- The registered sample project is clean.

## Local checks

Run these commands in order:

```powershell
pwsh -File .\scripts\start-bridge.ps1
pwsh -File .\scripts\doctor.ps1 -SkipTunnel
pwsh -File .\scripts\start-tunnel.ps1 -Initialize
pwsh -File .\scripts\doctor.ps1
```

Expected results:

- Bridge listens only on `127.0.0.1:46200/mcp`.
- `tunnel-client doctor --explain` succeeds.
- tunnel-client `/healthz` and `/readyz` return success.
- No public inbound listener is opened by the local process.
- No API key appears in command output, profile YAML, or repository files.

## ChatGPT remote flow

In ChatGPT developer mode, create or open the Tunnel-connected app and verify
tool discovery. Then perform these calls against the registered project:

1. `project_open`, `file_read`, and `code_search` for a read-only path.
2. `file_edit` with a deterministic replacement and explicit request hash.
3. `file_move` for one tracked file into a non-existing destination; confirm the
   source disappears, the destination preserves the content, both paths appear
   in the checkpoint diff, and replaying the same idempotency key does not move twice.
4. `file_delete` for one tracked regular file; confirm the path appears in the
   checkpoint diff, the file disappears, and replaying the same idempotency key
   does not delete anything else.
5. `registered_command_run` using a registered command ID; confirm unregistered
   command IDs are rejected. `test_run` and `format_run` remain compatibility wrappers.
6. `git_diff` and `operation_status`.
7. After user approval, `checkpoint_create` and, if needed,
   `checkpoint_restore` with the observed expected HEAD.

For every call record:

- ChatGPT request context;
- Bridge `request_id` and `operation_id`;
- final operation state;
- audit events and checkpoint IDs;
- changed files and Git HEAD before and after the mutation.

## Failure and recovery checks

Run each check separately and confirm the stated result:

| Action | Expected result |
| --- | --- |
| Stop `tunnel-client` before a new read | ChatGPT call fails; no local mutation starts |
| Stop Bridge while `tunnel-client` runs | tunnel readiness or MCP call fails; no replay occurs |
| Restart Bridge and run `doctor.ps1` | Bridge becomes healthy; existing blocked state is reported by Bridge lifecycle diagnostics |
| Send the same mutation idempotency key twice | No second mutation is executed |
| Change Git HEAD before rollback approval | Rollback fails closed with a checkpoint conflict |
| Point a local profile at a non-loopback MCP URL | `start-tunnel.ps1` rejects the profile |
| Put an API key in the profile YAML | `start-tunnel.ps1` rejects the profile |

This contract does not authorize creation, deletion, or modification of
OpenAI Platform tunnels. Those operations remain an explicit operator step.
