# codemcp-remote

[Simplified Chinese](README.zh-CN.md)

A policy-controlled local MCP bridge for using **ChatGPT as the only reasoning engine** while safely operating on registered local code repositories.

```text
ChatGPT
  -> ChatGPT Connector (Authentication = No authentication)
  -> OpenAI / ChatGPT Connector egress network
  -> Cloudflare Edge/WAF IP allowlist
  -> https://mcp.example.com/mcp
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200
  -> codemcp-remote network-trusted Bridge
  -> pinned codemcp worker (native Windows by default)
  -> registered local Git project
```

> **v0.1.0 release baseline:** product/runtime acceptance gates are closed for the first stable release. The release is intentionally `NotSigned`; GitHub-hosted CI remains `WAIVED / ACCEPTED RISK`. Exact release commit and artifact SHA-256 values are bound by build provenance, `SOURCE_COMMIT.txt`, `SHA256SUMS.txt`, and the GitHub Release rather than being self-referentially embedded in this source file.

Browse the [documentation center](docs/README.md) for current architecture,
operator guides, release gates, plans, and historical validation records.

## Why codemcp-remote

Remote code modification is a high-privilege operation. codemcp-remote deliberately exposes a smaller surface than arbitrary remote shell access:

- local projects must be explicitly registered;
- tool paths stay inside the registered project root;
- sensitive paths are denied by default;
- commands are selected by registered command ID, not arbitrary shell text or caller-supplied argv;
- mutations are idempotent and serialized per project;
- high-risk operations use short-lived, one-time approvals;
- mutations create Bridge-owned Git checkpoints;
- rollback uses compare-and-swap checks and fails closed on external Git changes;
- uncertain mutation outcomes remain `unknown` until explicitly reconciled;
- Bridge listens on loopback only.

The Bridge does not contain an agent loop or model provider. Repository content is treated as untrusted data and cannot authorize a privileged action.

## Current support

| Component | `v0.1.0` target |
|---|---|
| Host OS | Windows 11 x64-compatible |
| Installed runtime | packaged `codemcp-remote.exe`; no Python/uv/pwsh requirement |
| Mutation worker | Native Windows local worker |
| Development fallback | WSL2 Ubuntu remains source-mode compatibility only |
| codemcp | pinned `0.3.0` with Bridge-owned Windows compatibility wrapper |
| Remote transport | bundled Cloudflare Tunnel + external WAF network trust (recommended); OpenAI Secure MCP `tunnel-client` remains optional compatibility transport |
| Git | Git for Windows is an explicit runtime prerequisite |
| Identity model | Profile A is `network-only`; Profile B provides OAuth subject/client/scope identity |
| Native Windows Git-backed mutation | **supported and compatibility-tested** |
| Arbitrary shell / arbitrary path | **not exposed** |
| Automatic push / merge / rebase / deploy | **not supported** |

ChatGPT Connector availability depends on the capabilities enabled for your account/workspace. Cloudflare IP allowlisting is a network trust boundary, not authentication: it can restrict requests to published OpenAI/ChatGPT Connector egress ranges, but it does not identify a ChatGPT user, Workspace, account, or conversation. The Bridge's project, operation, approval, checkpoint, CAS, replay, audit, and loopback policies still apply.

See [`docs/reports/compatibility/codemcp-compatibility-matrix.md`](docs/reports/compatibility/codemcp-compatibility-matrix.md) for the tested backend behavior.

## Requirements

### Installed Windows release

The `codemcp-remote-setup.exe` release requires:

- Windows 11 x64-compatible;
- Git for Windows;
- for the recommended path, a user-owned Cloudflare Tunnel and a Cloudflare WAF IP List populated from the official OpenAI Connector egress manifest;
- ChatGPT Connector access with `Authentication = No authentication`.

The installed runtime includes the Bridge, native codemcp worker, `cloudflared`, and the optional OpenAI `tunnel-client`. It does **not** require Python, `uv`, PowerShell 7, WSL2, or the codemcp-remote source repository.

### Source development

Building or running from this repository additionally requires:

- Python 3.12+ on Windows;
- PowerShell 7 (`pwsh`);
- [`uv`](https://docs.astral.sh/uv/).

WSL2 is optional and only needed when explicitly testing the compatibility fallback worker.

Do not store the Tunnel runtime API key in this repository, a local env file, the generated Tunnel profile, shell history, or logs.

### Recommended personal deployment: Cloudflare network trust

Profile A is intended for one operator using a private ChatGPT Connector:

```toml
[auth]
mode = "none"

[network_trust]
mode = "cloudflare-chatgpt"
allowed_hosts = ["mcp.example.com"]
allowed_origins = ["https://chatgpt.com"] # optional, if-present validation
```

The Cloudflare Edge/WAF rule must protect the dedicated hostname before Tunnel ingress. A typical rule is:

```text
(http.host eq "mcp.example.com" and not ip.src in $chatgpt_connectors)
```

`$chatgpt_connectors` is an operator-managed Cloudflare IP List sourced from the official OpenAI Connector manifest; current ranges are deliberately not hardcoded in this repository. Do not implement the allowlist with `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, or `Cf-Access-*` in Python. Keep the Bridge on `127.0.0.1:46200`.

The allowlist proves only network provenance. It is not authentication, user identity, or strong identity. Profile A reports `identity_level = network-only`; use Profile B below when subject/client/scope identity is required.

Initialize an installed runtime without an OAuth verification secret. For a packaged Windows EXE, the installation directory is the default runtime home, so normal installed commands do not need `--home`:

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"
$env:TUNNEL_TOKEN = "<read from your secret manager; do not paste into chat>"

& $exe init `
  --transport cloudflare `
  --public-url "https://mcp.example.com/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host mcp.example.com `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret
$env:TUNNEL_TOKEN = $null

& $exe project add my_project "D:\workspace\my-project"
# The running Bridge observes the validated registry change automatically.
# No Bridge, Tunnel, or ChatGPT Connector restart is required.
& $exe doctor
& $exe start
```

The tunnel token is stored with Windows DPAPI. `CODEMCP_RS_VERIFICATION_SECRET` is not required for Profile A. `--home` still overrides `CODEMCP_HOME`, and `CODEMCP_HOME` overrides the packaged EXE-directory default; `--app-root` remains a legacy compatibility option. `doctor` should show Cloudflare, `auth.mode = none`, network trust ready, the exact allowed host, `identity_level = network-only`, and a DPAPI tunnel-secret source.

`/healthz` is a lifecycle probe, not a public application endpoint. Review Tunnel ingress separately so it is not exposed as an unintended public route.

## Installed release quick start

After installing Git for Windows and `codemcp-remote-setup.exe`, the recommended quick start is the Profile A command sequence above. After first-time initialization, double-clicking `codemcp-remote.exe` starts the managed lifecycle; `codemcp-start.cmd` and `codemcp-stop.cmd` provide explicit one-click controls. See the complete [Windows build, install, and use guide](docs/guides/windows-build-install-use.md).

The older OpenAI Secure MCP Tunnel remains available as an explicit compatibility transport:

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"

# Set CONTROL_PLANE_API_KEY only in the current process from your secret source.
& $exe init --tunnel-id "<your tunnel id>" --store-api-key
$env:CONTROL_PLANE_API_KEY = $null

& $exe project add my_project "D:\workspace\my-project"
& $exe doctor
& $exe start
& $exe status
```

`--store-api-key` uses Windows DPAPI. Do not paste the API key into chat or store it in the Tunnel env/profile files. This compatibility path is transport-specific and does not replace the Cloudflare network-trust profile for the recommended public deployment.

For clean-machine release validation, use [`docs/releases/v0.1.0/packaging-phase-5-clean-machine-validation.md`](docs/releases/v0.1.0/packaging-phase-5-clean-machine-validation.md).

## Source development quick start

### 1. Install the Bridge dependencies

From the repository root:

```powershell
uv sync --project bridge
```

### 2. Optional: prepare the WSL2 fallback worker

Native Windows is the default worker mode and requires no WSL2 bootstrap. If you
explicitly configure `codemcp.worker_mode = "wsl2"`, prepare the fallback runtime:

```powershell
pwsh -File .\scripts\bootstrap-wsl.ps1
```

The bootstrap exports the locked non-development dependency set from `bridge/uv.lock`,
creates `.local/bridge-venv-wsl`, installs the worker dependencies, and verifies
`codemcp==0.3.0`.

### 3. Register your first project

Project registration is a **local administrative operation**. Use the local CLI to add or remove authorized roots; MCP clients cannot add, remove, reload, or reconfigure projects.

For source-mode development, create the Git-ignored local registry once if it does not already exist:

```powershell
Copy-Item config/projects.example.toml config/projects.toml
```

Then register the project through the local CLI:

```powershell
uv run --project bridge codemcp-bridge-server project add my_project "D:\workspace\my-project"
```

When the Bridge is already running, the validated `projects.toml` update is detected automatically on the next authorization-sensitive request. No Bridge, Tunnel, or ChatGPT Connector restart is required.

Use direct `projects.toml` editing only for trusted offline maintenance or recovery. Normal project authorization changes should go through the local CLI so validation and atomic replacement are preserved. Registered command configuration remains trusted local policy.

Only register repositories you intend ChatGPT to access.

### 4. Prepare the recommended Cloudflare source profile

Ensure `cloudflared` is available beside the source runtime or on `PATH`. The repository helper can prepare the pinned client when needed:

```powershell
pwsh -File .\scripts\prepare-cloudflared.ps1
```

Use an explicit source runtime home and keep the tunnel token only in the current process during initialization:

```powershell
$runtimeHome = Join-Path $PWD ".local\source-runtime"
$env:TUNNEL_TOKEN = "<load locally from a secret manager>"

uv run --project bridge codemcp-bridge-server init --home $runtimeHome `
  --transport cloudflare `
  --public-url "https://<your-mcp-host>/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host <your-mcp-host> `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret

$env:TUNNEL_TOKEN = $null
```

For the optional OpenAI Secure MCP compatibility transport, follow [`docs/guides/tunnel-setup.md`](docs/guides/tunnel-setup.md) instead. It is not the default `v0.1.0` source or installed deployment path.

### 5. Start and diagnose

Use the same managed lifecycle from source mode:

```powershell
uv run --project bridge codemcp-bridge-server doctor --home $runtimeHome
uv run --project bridge codemcp-bridge-server start --home $runtimeHome
uv run --project bridge codemcp-bridge-server status --home $runtimeHome
```

Stop the source-mode managed lifecycle with:

```powershell
uv run --project bridge codemcp-bridge-server stop --home $runtimeHome
```

The PowerShell `start-all.ps1` / `doctor.ps1` / `stop-all.ps1` helpers remain available for development and compatibility testing, but they are not the installed-product runtime contract.

## Connect from ChatGPT

For the recommended path, follow [`docs/guides/cloudflare-tunnel-setup.md`](docs/guides/cloudflare-tunnel-setup.md):

1. configure the Cloudflare IP List and whole-host WAF rule;
2. keep the local Bridge and `cloudflared` healthy;
3. create/use a ChatGPT Connector with `Authentication = No authentication` and the public `/mcp` URL;
4. confirm that the Bridge tools are discovered;
5. run the remote contract in [`tests/e2e/test_tunnel_contract.md`](tests/e2e/test_tunnel_contract.md).

Profile B keeps the existing OAuth Resource Server interoperability path for multi-user, enterprise, or subject/scope-aware deployments. It continues to use `auth.mode = "oauth-resource-server"`, `mcp-rs-verification-v1`, RFC 9728 metadata/challenges, and an independently deployed `mcp-auth-server`.

A safe first request is read-only: open one registered project, inspect its status, then read a non-sensitive source file.

Before the first mutation, make sure the target branch and worktree are exactly the state you expect. The default policy rejects a dirty worktree.

## Mutation, approval, and recovery

Mutation calls require a caller request ID and a canonical SHA-256 request hash. Repeating an already completed mutation with the same identity replays the persisted result instead of executing it twice.

Commands or Git operations that require approval return a short-lived one-time approval flow. Plaintext approval tokens are not persisted in SQLite.

For file mutations, the first successful mutation in an eligible Bridge session
creates a branch-visible WIP commit with a
`Codemcp-Remote-Session: <session_id>` footer. Later mutations amend that WIP
only when the Bridge can prove the same session, branch, clean HEAD, finalized
successful checkpoint, exact footer, and absence of locally observable shared
refs. The Bridge rechecks branch, HEAD, and shared refs immediately before an
amend and records an unknown result if finalization observes a different HEAD.
Missing or uncertain ownership evidence during mode selection safely creates a
new commit; a race discovered after a file side effect starts remains `unknown`
for explicit reconciliation. No-op content changes do not create or transfer
WIP ownership.

Before mutation, the Bridge records a Git baseline and creates a Bridge-owned checkpoint. Checkpoint restore:

- is scoped to the registered project/session;
- verifies the registered checkpoint ref;
- requires the expected current HEAD;
- requires a clean worktree;
- requires explicit approval;
- creates a rollback safety checkpoint;
- refuses to overwrite externally changed Git state.

If the Bridge cannot prove whether a side effect occurred, the operation remains `unknown`; do not blindly retry it. Inspect `operation_status` and use the explicit reconciliation flow.

The branch WIP commit and the Bridge checkpoint ref serve different purposes:
the commit is the visible Git baseline for the active session, while each
mutation checkpoint retains the exact pre-mutation commit for audit, diff, and
restore. Checkpoint refs are Bridge-owned recovery metadata and are not a
publication mechanism. Commits or checkpoints created before session WIP
footers were introduced are never automatically adopted for amend.

Local branch, tag, and remote-tracking refs are the shared refs the Bridge can
inspect. The Bridge cannot prove that a commit was never pushed to a remote
state not represented locally, so operators must not publish an active session's
WIP before its mutations are complete.

See [`docs/architecture/git-policy.md`](docs/architecture/git-policy.md) and [`docs/architecture/security-model.md`](docs/architecture/security-model.md).

## Doctor and operations

For an installed release, the main operator commands are:

```powershell
& $exe doctor
& $exe start
& $exe status
& $exe stop
```

The detailed operator guide is [`docs/guides/operations-runbook.md`](docs/guides/operations-runbook.md).

Source-development helpers remain available:

```powershell
pwsh -File .\scripts\doctor.ps1
pwsh -File .\scripts\start-all.ps1
pwsh -File .\scripts\stop-all.ps1
pwsh -File .\scripts\validate-lifecycle.ps1 -Iterations 20
```

Those PowerShell scripts are supporting source/compatibility tooling. Stable release requires the packaged 20-cycle lifecycle, crash, secret-canary, Windows path/encoding, transport-disconnect, and security matrix in [`docs/acceptance/phase-6-validation.md`](docs/acceptance/phase-6-validation.md).

## Security model

Read these before exposing a local repository through the Bridge:

- [`SECURITY.md`](SECURITY.md) — vulnerability reporting;
- [`docs/architecture/security-model.md`](docs/architecture/security-model.md) — trust boundaries and guarantees;
- [`docs/architecture/threat-model.md`](docs/architecture/threat-model.md) — threats, mitigations, and residual risks;
- [`docs/architecture/git-policy.md`](docs/architecture/git-policy.md) — checkpoint/diff/rollback constraints.

Important boundaries:

- the local OS account and trusted local configuration are root trust assumptions;
- a dangerously configured registered command is still dangerous;
- filename filtering cannot identify every secret stored under an ordinary filename;
- a compromised dependency/toolchain is not fully contained by the Bridge;
- this first release is not a multi-user identity/RBAC system.

Security issues should follow [`SECURITY.md`](SECURITY.md), not a public exploit report.

## Development

Local checks:

```powershell
uv sync --project bridge
uv run --project bridge codemcp-bridge doctor --strict --json
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests tests/integration
uv run --project bridge pytest -q bridge/tests tests/integration
```

The final stable release additionally requires the gates in:

- [`docs/plans/v0.1.0/open-source-readiness-plan.md`](docs/plans/v0.1.0/open-source-readiness-plan.md)
- [`docs/acceptance/phase-6-validation.md`](docs/acceptance/phase-6-validation.md)
- [`docs/acceptance/acceptance-test-plan.md`](docs/acceptance/acceptance-test-plan.md)

## Contributing

Contributions must preserve the project's fail-closed security model. In particular, changes that widen filesystem, command, identity, transport, model-egress, or destructive Git scope require an explicit threat-model update and negative tests.

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), and [`CHANGELOG.md`](CHANGELOG.md).

## License

codemcp-remote is licensed under the **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`). See [`LICENSE`](LICENSE).

`codemcp==0.3.0` is a separate third-party dependency with a documented upstream metadata discrepancy: its distribution metadata reports MIT while the bundled audited `License-File` is Apache-2.0. The release preserves both facts and the bundled license evidence; see [`docs/reports/testing/v0.1.0-dependency-license-compatibility-signoff.md`](docs/reports/testing/v0.1.0-dependency-license-compatibility-signoff.md). Other dependency licenses are reviewed as part of the release supply-chain gate.
