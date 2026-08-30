# Operations Runbook

> Updated: 2026-08-28  
> Scope: current `v0.1.0` Windows operation and recovery

This runbook describes the current operator path. Detailed build/install instructions are in [`windows-build-install-use.md`](windows-build-install-use.md).

## 1. Supported operating modes

### Recommended installed mode

```text
Windows 11
  + codemcp-remote.exe
  + Git for Windows
  + Native Windows worker
  + Cloudflare Tunnel
  + Profile A network trust
```

Normal installed operation does not require Python, `uv`, PowerShell 7 or WSL2.

### Source-development mode

Source development requires:

- Python 3.12+;
- `uv`;
- PowerShell 7;
- Git for Windows.

WSL2 is optional and used only when explicitly testing the compatibility fallback worker.

OpenAI Secure MCP Tunnel is an optional compatibility transport.

## 2. First-time installed initialization

Recommended Profile A:

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"
$env:TUNNEL_TOKEN = "<load locally from a secret manager>"

& $exe init `
  --transport cloudflare `
  --public-url "https://<your-mcp-host>/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host <your-mcp-host> `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret

$env:TUNNEL_TOKEN = $null
```

The Cloudflare Tunnel token is stored using Windows DPAPI when `--store-transport-secret` is selected.

Do not place the plaintext token in:

- the repository;
- example configuration;
- command history copied into public logs/issues;
- ChatGPT messages;
- unrestricted runtime logs.

Profile A is network trust only. It does not identify a human user, ChatGPT account, Workspace or conversation.

## 3. Project registration

Project registration is a local administrative control-plane operation.

Add:

```powershell
& $exe project add my-project "D:\workspace\my-project"
```

Remove with expected-root protection:

```powershell
& $exe project remove my-project `
  --expected-root "D:\workspace\my-project"
```

A running Bridge observes validated registry changes automatically.

Normal project add/remove does not require restarting:

- Bridge;
- Tunnel;
- installed EXE lifecycle;
- ChatGPT Connector.

MCP clients cannot add, remove, reload or reconfigure project authorization.

Direct editing of `projects.toml` is reserved for trusted offline maintenance or recovery.

## 4. Normal lifecycle

### Diagnose before start

```powershell
& $exe doctor
```

For the recommended Profile A, verify at least:

- configuration is valid;
- Bridge bind is loopback;
- Git prerequisite is available;
- `worker_mode = local`;
- transport is Cloudflare;
- `auth.mode = none`;
- `network_trust.mode = cloudflare-chatgpt`;
- exact allowed host is present;
- `identity_level = network-only`;
- transport credential source is protected as expected.

### Start

```powershell
& $exe start
```

The managed lifecycle starts the loopback Bridge and the configured remote transport.

The Native Windows codemcp worker is started on demand when an operation needs it.

### Status

```powershell
& $exe status
```

Status output should expose bounded/sanitized operational state, not raw project roots, registered command argv or secret values through public health data.

### Stop

```powershell
& $exe stop
```

Stop only product-owned process trees.

An unrelated process that happens to occupy a managed port must never be killed merely because of the port number.

The packaged one-click launchers may also be used:

```text
codemcp-start.cmd
codemcp-stop.cmd
```

## 5. Recommended Cloudflare boundary

The public path is:

```text
ChatGPT Connector
  -> OpenAI/ChatGPT Connector egress
  -> Cloudflare WAF IP allowlist
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200/mcp
```

Requirements:

- Bridge stays on loopback;
- Cloudflare origin targets the loopback MCP endpoint;
- WAF/IP admission happens at Cloudflare Edge;
- current Connector egress ranges are managed externally rather than hardcoded in the Bridge;
- `/healthz` must not be unintentionally exposed as a public application endpoint;
- forwarded client-IP headers are not an authorization source.

An ordinary public request should be blocked before it reaches the Bridge.

## 6. Logs and local state

Runtime logs, SQLite state, registry configuration and validation evidence are local operational data.

Do not commit:

- runtime logs;
- `.local/`;
- SQLite databases;
- DPAPI secret files;
- local transport profiles containing private state;
- real `projects.toml`;
- acceptance credentials.

Logs must redact common secret forms, including:

- `TUNNEL_TOKEN`;
- Bearer tokens;
- API-key-shaped values;
- approval tokens;
- optional OAuth verification secrets;
- `CONTROL_PLANE_API_KEY` on the Secure MCP compatibility path.

Worker stderr and command output must remain bounded.

Denied secret-file content must not appear in unrestricted diagnostics.

## 7. Common failure handling

### Bridge unhealthy

Run:

```powershell
& $exe doctor
& $exe status
```

Then stop/start the owned lifecycle if required.

A restart must not silently replay a mutation whose backend outcome is uncertain.

### Cloudflare Tunnel unhealthy

Verify:

- transport configuration;
- protected tunnel-token source;
- public URL;
- loopback origin;
- local metrics/health state;
- external Cloudflare Tunnel/WAF configuration.

The local Bridge can remain diagnosable even when the public transport is unavailable.

### Git unavailable

Git for Windows is a product runtime prerequisite.

If Git cannot be resolved, protected mutation must not run.

Fix the runtime prerequisite, rerun `doctor`, then resume.

### Native worker failure

A worker failure during a mutation must surface as failure or `unknown` according to the known side-effect boundary.

Do not retry an uncertain mutation with a new identity until the prior operation has been reconciled.

### Project becomes blocked

Inspect:

```text
operation_status
git_status
```

If a mutation is `unknown`, use the explicit `operation_reconcile` flow with repository evidence.

Do not manually clear SQLite operation state.

### Approval pending

High-risk operations use short-lived one-time approval tokens.

Plaintext approval tokens are not persisted.

A restart or stale approval must fail closed rather than reconstructing plaintext approval state.

## 8. Checkpoint and rollback operation

Before mutation, the Bridge creates or records the protected Git baseline and Bridge-owned checkpoint evidence.

For manual restore:

1. inspect `git_status`;
2. inspect the target checkpoint/diff as required;
3. request `checkpoint_restore` with the current expected HEAD;
4. complete explicit approval;
5. verify final branch/HEAD and clean worktree.

Restore must reject:

- wrong project/session scope;
- dirty worktree;
- external branch change;
- external HEAD change;
- missing/tampered checkpoint ref;
- stale expected HEAD.

An uncertain Git reset outcome becomes `UNKNOWN_SIDE_EFFECT`.

## 9. Source-development operations

From the repository root:

```powershell
uv sync --project bridge
uv run --project bridge codemcp-bridge doctor --json
uv run --project bridge pytest -q
```

Source lifecycle helpers remain available:

```powershell
pwsh -File .\scripts\start-bridge.ps1
pwsh -File .\scripts\start-all.ps1
pwsh -File .\scripts\doctor.ps1
pwsh -File .\scripts\stop-all.ps1
```

These scripts are development/compatibility operational tools. They must not be interpreted as evidence that the installed product requires PowerShell 7 or a source checkout.

The source lifecycle supports the configured worker mode; Native Windows is the default and WSL2 is an explicit fallback.

## 10. Compatibility paths

### WSL2 worker fallback

When explicitly selecting WSL2:

```toml
[codemcp]
worker_mode = "wsl2"
```

prepare the source-mode worker environment as documented by the repository.

WSL-specific failures apply only to this compatibility mode.

### OpenAI Secure MCP Tunnel

The older Secure MCP path remains available for compatibility.

Its wrapper must continue to enforce:

- OpenAI control plane;
- loopback MCP target;
- no plaintext API key in the profile;
- no direct codemcp exposure.

This is not the recommended `v0.1.0` personal public path.

### OAuth Resource Server Profile B

Use Profile B only when subject/client/scope identity is required.

Profile B identity semantics must remain separate from Profile A `network-only` identity.

## 11. Phase 6 release validation

The authoritative release matrix is:

[`../acceptance/phase-6-validation.md`](../acceptance/phase-6-validation.md)

Stable release requires more than one successful start.

Mandatory evidence includes:

- 20/20 packaged lifecycle cycles;
- Bridge/Cloudflare/native-worker crash recovery;
- unrelated listener protection;
- Git/transport credential failures;
- timeout/process-tree cleanup;
- secret/log canaries;
- Windows path/encoding matrix;
- dependency upgrade/rollback review.

The repository source runner remains supporting evidence:

```powershell
pwsh -File .\scripts\validate-lifecycle.ps1 -Iterations 20
```

but it does not replace the packaged-runtime release gate.

## 12. Dependency rollback

Before a dependency upgrade:

- record the clean known-good commit;
- preserve the lock file;
- run the full automated suite and compatibility matrix;
- compare the public 22-tool contract;
- rerun affected Phase 6/7 security and reliability gates.

If rollback is required:

1. stop owned processes;
2. restore known-good dependency metadata/lock;
3. rebuild from the known-good commit;
4. rerun `doctor`;
5. rerun tests;
6. rerun at least one packaged lifecycle cycle;
7. reconcile any repository operation that was already `unknown`.

Dependency rollback does not prove that an uncertain repository mutation was undone.

## 13. Release-state rule

Cloudflare Profile A Phase A-H live acceptance is complete and the current connector is usable for controlled operation.

Stable `v0.1.0` is still blocked until the repository-wide Phase 6/7, supply-chain, strict clean-machine packaging and hosted CI gates pass.
