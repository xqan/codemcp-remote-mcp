# Packaging Phase 5 — Clean Windows Release Validation

Date: 2026-08-26

Status: **LIVE ACCEPTANCE COMPLETE; STRICT PACKAGING PHASE 5 PASS NOT CLAIMED; STABLE v0.1.0 REMAINS BLOCKED BY PHASE 7**

## Objective

Validate the packaged `codemcp-remote-setup.exe` on a clean Windows 11 x64 host or VM, independent of the source tree and development runtime.

Packaging Phase 5 must prove that the installed product can:

1. install from the Phase 4 installer;
2. run without Python, `uv`, PowerShell 7, or WSL2 on the product runtime `PATH`;
3. initialize a selected writable runtime home using `--home` or `CODEMCP_HOME`;
4. use Profile A (`auth.mode = none` + Cloudflare network trust) without an OAuth verification secret;
5. store the Cloudflare `TUNNEL_TOKEN` with Windows DPAPI and continue after the plaintext process environment value is removed;
6. preserve the optional Profile B OAuth Resource Server path and its DPAPI verification secret behavior;
7. use the native local codemcp worker and bundled transport clients;
8. register and operate on a disposable local Git repository;
9. start an owned, healthy Bridge and Tunnel;
10. complete the remote ChatGPT Connector contract against that clean-machine repository when Phase H is authorized;
11. stop and uninstall without deleting preserved runtime/user data.

Phase 5 does not change the application architecture or installer payload unless clean-machine validation exposes a release blocker. Cloudflare WAF/IP List state is external deployment state and is not provisioned by the installer.

## Current acceptance profiles

The clean Windows harness supports two explicit Cloudflare profiles:

| Profile | Purpose | Required configuration | Secret requirement |
| --- | --- | --- | --- |
| `5.5.7A` | Recommended personal deployment | `auth.mode = "none"`, `network_trust.mode = "cloudflare-chatgpt"`, non-empty exact `allowed_hosts` | `TUNNEL_TOKEN` only; stored with Windows DPAPI |
| `5.5.7B` | Optional advanced/enterprise interoperability | existing `auth.mode = "oauth-resource-server"`, issuer/resource/validation contract | `TUNNEL_TOKEN` plus `CODEMCP_RS_VERIFICATION_SECRET`, both DPAPI-backed |

Profile A uses ChatGPT Connector `Authentication = No authentication`. The Cloudflare IP List/WAF rule is a network trust boundary, not authentication or user identity; it cannot identify a ChatGPT user, Workspace, account, or conversation. Profile B retains `mcp-rs-verification-v1`, RFC 9728 behavior, and external OAuth subject/client/scope semantics. Existing 5.5.7B evidence is preserved and is not marked failed.

The harness defaults to `5.5.7A`:

```powershell
pwsh -NoLogo -NoProfile -File .\scripts\validate-clean-windows-release.ps1 `
  -Action Prepare `
  -AcceptanceProfile 5.5.7A `
  -Transport cloudflare `
  -PublicUrl 'https://codemcp.quickclip.cc/mcp' `
  -AllowedHost codemcp.quickclip.cc
```

Use `-AcceptanceProfile 5.5.7B` only when the external OAuth Resource Server evidence is intentionally being repeated. Profile A must not require `CODEMCP_RS_VERIFICATION_SECRET`.

## Runtime prerequisite boundary

The installed v0.1.0 target does **not** require:

- Python;
- `uv`;
- PowerShell 7 (`pwsh`);
- WSL2;
- the codemcp-remote source repository;
- a separately installed `tunnel-client`.

The installed v0.1.0 target **does require Git for Windows**.

This is an intentional release prerequisite, not a development-only convenience. Git is part of the safety model used for clean-worktree checks, mutation baselines, Bridge-owned checkpoints, session WIP commits, compare-and-swap rollback, and reconciliation. The current installer does not redistribute Git.

The Phase 5 harness rewrites the product runtime `PATH` so that only the installed product directory, the resolved Git directory, and Windows system directories remain visible. It fails if `python.exe`, `py.exe`, `uv.exe`, or `pwsh.exe` are still resolvable after that isolation. `wsl.exe` may remain visible because it is a Windows system component, but the acceptance contract requires `doctor.checks.configuration.worker_mode == "local"` and never configures the WSL2 worker.

## Release artifact under test

Phase 4 produced:

```text
codemcp-remote-setup.exe
SHA-256:
659651d9c0c1f333c39bf1ae4cee107c99bf147487f855aca4600309ec39c37c
```

The exact installer hash must be supplied to the clean-machine harness. A mismatch fails before installation.

On 2026-08-25, `scripts/prepare-windows-release-candidate.ps1` completed successfully and produced the exact clean-machine release candidate:

```text
codemcp-remote-v0.1.0-windows-x64.zip
SHA-256:
6974548d300356dff8219fcb7c84b0ce0ae618cfaa9f64e65424603587ad4168

embedded installer SHA-256:
659651d9c0c1f333c39bf1ae4cee107c99bf147487f855aca4600309ec39c37c

Authenticode:
NotSigned
```

The release-candidate gate also parsed the clean-machine harness with Windows PowerShell 5.1 before creating the archive. This gate is PASS. Phase 5 remains open until a separate clean Windows 11 host/VM completes `Prepare`, `Start`, the remote connector contract, and `Cleanup`.

## Secret handling

Never pass a runtime secret as a script parameter or command-line argument. The harness accepts secrets only from the current PowerShell process environment and clears them before the final doctor/start proof.

For the recommended Profile A, provide only the Cloudflare tunnel token:

```powershell
$env:TUNNEL_TOKEN = "<set locally; do not paste into chat>"
```

`Prepare` initializes the equivalent of:

```text
codemcp-remote.exe init --home <home> --transport cloudflare --public-url <public-url>/mcp `
  --auth-mode none --network-trust cloudflare-chatgpt `
  --allowed-host <exact-host> --store-transport-secret
```

The product stores the tunnel token with Windows DPAPI. Profile A does not require `CODEMCP_RS_VERIFICATION_SECRET`. After the environment value is removed, `doctor` must report:

```text
checks.tunnel_token.status = ok
checks.tunnel_token.source = windows-dpapi
checks.auth.mode = none
checks.auth.oauth_secret_required = false
checks.network_trust.mode = cloudflare-chatgpt
checks.identity_level = network-only
```

For Profile B only, also provide `CODEMCP_RS_VERIFICATION_SECRET` in the process environment. The harness stores it with DPAPI and requires the existing OAuth Resource Server doctor contract, including `mcp-rs-verification-v1` and `secret_source = windows-dpapi`.

## Acceptance harness

The standalone harness is:

```text
scripts\validate-clean-windows-release.ps1
```

It is intentionally Windows PowerShell 5.1-compatible so the clean host does not need PowerShell 7.

The harness has four actions: `Prepare`, `Start`, `Cleanup`, and `Reset`. `Prepare` and `Start` accept `-AcceptanceProfile 5.5.7A` (default) or `5.5.7B`.

### 1. Prepare

`Prepare`:

- allows a first install on a host with no existing codemcp-remote installation, or a controlled
  same-AppId upgrade when the fixed Phase 5.5.7 state proves that the existing installation is
  harness-managed; unrelated or unowned installations fail closed;
- verifies the installer SHA-256;
- requires Git for Windows;
- silently installs to the default per-user location;
- verifies required installed files;
- verifies the installed executable against its packaged `SHA256SUMS.txt` identity manifest and
  records current/previous installer identity in the non-secret Phase 5 state;
- rejects bundled Python/uv/pwsh/WSL executables;
- isolates the runtime `PATH`;
- checks `codemcp-remote.exe 0.1.0`;
- initializes the selected runtime home with `--home`;
- for 5.5.7A, initializes Cloudflare + No-Auth + network trust and stores only the tunnel token with DPAPI;
- for 5.5.7B, initializes the existing Cloudflare OAuth Resource Server profile and stores the tunnel and verification secrets with DPAPI;
- removes plaintext environment values before the final `doctor` check;
- verifies profile-aware `doctor` output;
- creates a disposable Git repository;
- registers that project;
- records non-secret Phase 5.5.7 state under `%LOCALAPPDATA%\codemcp-remote\phase5-validation.json`;
- deliberately leaves Bridge/Tunnel stopped.

Expected terminal state:

```json
{
  "status": "ready-for-start",
  "phase": "5.5.7",
  "acceptance_profile": "5.5.7A",
  "action": "prepare",
  "worker_mode": "local",
  "transport": "cloudflare",
  "auth_mode": "none",
  "network_trust_mode": "cloudflare-chatgpt",
  "identity_level": "network-only",
  "transport_secret_source": "windows-dpapi"
}
```

### 2. Start

`Start` re-applies the isolated runtime `PATH`, verifies `doctor`, then starts Bridge and Tunnel.

Expected terminal state:

```json
{
  "status": "ready-for-remote-verification",
  "phase": "5.5.7",
  "acceptance_profile": "5.5.7A",
  "action": "start",
  "auth_mode": "none",
  "network_trust_mode": "cloudflare-chatgpt",
  "identity_level": "network-only",
  "bridge_health": "ok",
  "tunnel_health": "ok"
}
```

For 5.5.7A, configure ChatGPT Connector with `Authentication = No authentication` and the public `/mcp` URL only after `Start` is healthy. For 5.5.7B, use the existing OAuth Connector contract. This split is intentional. If the same Cloudflare Tunnel is currently used by the development machine, stop the development lifecycle after `Prepare` and before `Start`. Prefer a dedicated acceptance Tunnel/Connector when available.

### 3. Cleanup

After remote verification:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\validate-clean-windows-release.ps1 -Action Cleanup
```

Cleanup stops the installed lifecycle and runs the Inno Setup uninstaller. It intentionally preserves `%LOCALAPPDATA%\codemcp-remote` and the disposable project because the product uninstall contract preserves user/runtime data.

### 4. Reset for acceptance retries only

`Reset` is not part of normal product uninstall behavior. It exists only so the same clean Windows VM can safely retry Phase 5 after a failed acceptance attempt.

Run it only after `Cleanup`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\validate-clean-windows-release.ps1 -Action Reset
```

`Reset` fails if codemcp-remote is still installed. It can remove only these fixed acceptance roots:

```text
%LOCALAPPDATA%\codemcp-remote
%LOCALAPPDATA%\codemcp-remote-phase5
```

It refuses other paths and refuses reparse-point roots. A custom `-ProjectRoot` outside the default acceptance tree is never deleted automatically.

The first clean-machine attempt on 2026-08-25 exposed a harness-only isolation defect: the PATH gate kept both `System32` and `%SystemRoot%`, which reintroduced the Windows Python Launcher at `C:\Windows\py.exe`. The product installer had already succeeded, but `Prepare` correctly stopped before initialization. The harness now keeps only the installed product directory, the resolved Git directory, and `System32`; `%SystemRoot%` is no longer added.

## Disposable repository

Default project identity:

```text
project_id: phase5-clean
project root:
%LOCALAPPDATA%\codemcp-remote-phase5\project
```

The harness creates and commits:

```text
README.md
pyproject.toml
PHASE5_ACCEPTANCE.txt
```

`pyproject.toml` is a metadata-only marker that lets the Bridge deterministically detect the disposable repository as the built-in `python` profile. The acceptance contract does not execute Python commands on the clean machine; Python remains absent from the isolated runtime `PATH`. The project intentionally omits `codemcp.toml` so the Bridge can validate its generated fixed command catalog without PowerShell 5.1 UTF-8 BOM ambiguity.

The baseline commit hash is returned by `Prepare` and persisted in the Phase 5 validation state.

## Remote Connector contract

Phase 5 is not closed by local installation checks alone.

After `Start`, ChatGPT must connect through the intended Cloudflare URL and perform the following against `phase5-clean`:

1. `project_open` succeeds.
2. `project_status` reports the disposable project and `development_ready=true`.
3. `file_read` reads `PHASE5_ACCEPTANCE.txt`.
4. `git_status` reports the baseline branch/HEAD and a clean worktree.
5. One deterministic text mutation is performed on the disposable repository.
6. The resulting Git/checkpoint state is inspected.
7. Idempotent replay is checked for the same mutation identity.
8. The repository is restored to its original baseline using the normal approval/checkpoint restore path.
9. Final `git_status` proves the original baseline HEAD and a clean worktree.
10. For Profile A, the request succeeds without `Authorization` and the Cloudflare WAF event shows network admission only; no user identity claim is recorded.
11. For Profile B, the existing OAuth subject/client/scope and RFC 9728 Resource Server checks remain intact.

No real user repository is used for this test.

## Cloudflare cutover options

### Preferred: dedicated acceptance Cloudflare Tunnel

Use a separate Cloudflare Tunnel/hostname and Connector for the clean-machine host. This avoids ambiguity and leaves the development Connector running.

### Fallback: reuse the current Cloudflare Tunnel

1. Run `Prepare` on the clean machine.
2. Stop the current development-machine `codemcp-remote.exe` lifecycle.
3. Run `Start` on the clean machine.
4. Wait for the existing connector to reconnect.
5. Run the remote contract.
6. Run `Cleanup` on the clean machine.
7. Restore the development lifecycle if needed.

Do not run two active clients for the same acceptance path when the result would be ambiguous. Verify the normal-IP WAF `403` and Cloudflare Security Events `BLOCK`/`ALLOW` evidence separately; they are Phase H live checks.

## Historical OAuth-first release evidence

The following artifact and Prepare record are retained evidence from the pre-5.5.7A OAuth-first harness. They are Profile B/history, not proof of the current Profile A live path.

## Current release candidate reference

The Phase 5.5.7 release-candidate package was regenerated after the clean-machine PATH isolation,
reset-harness, and project-registration rerun fixes.

```text
candidate:
codemcp-remote-v0.1.0-windows-x64.zip

candidate ZIP SHA-256:
see the adjacent `.zip.sha256` manifest generated by the release workflow

installer SHA-256:
b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e

authenticode:
NotSigned
```

The candidate generation gate also revalidated the clean-machine harness with the Windows PowerShell
5.1 parser. The packaged CLI now includes an expected-root ownership-checked `project remove` operation
so Prepare can safely rebuild its fixed `phase5-clean` registration on rerun. A matching registration is
removed before a fresh baseline; a different root fails closed. DPAPI transport/auth credentials are
preserved, and custom project roots are rejected rather than managed automatically.

## Historical clean-machine Prepare validation (Profile B)

The historical clean-machine `Prepare` gate passed on Windows 11 with the release candidate above. It validated the OAuth-first compatibility profile and remains useful evidence for 5.5.7B; it does not establish the current ChatGPT No-Auth/WAF acceptance.

Recorded result:

```text
status: ready-for-start
installer SHA-256:
659651d9c0c1f333c39bf1ae4cee107c99bf147487f855aca4600309ec39c37c
install root:
%LOCALAPPDATA%\Programs\codemcp-remote
project id:
phase5-clean
baseline HEAD:
7985f14dde3c33762b6e318ec38fc7dbe806fa1b
worker mode:
local
Git:
D:\soft\PortableGit\bin\git.exe
API key source:
windows-dpapi
tunnel-client:
bundled installed executable
python visible on isolated PATH:
false
uv visible on isolated PATH:
false
pwsh visible on isolated PATH:
false
```

`wsl.exe` remained visible as a Windows system component, but the validated Bridge configuration reported `worker_mode=local`; the acceptance path does not configure or invoke the WSL2 worker.

The Git LF-to-CRLF message emitted while creating the disposable repository is an informational Windows Git working-tree conversion warning and does not change the committed baseline identity recorded above.

Bridge and Tunnel remain intentionally stopped after `Prepare`. The next gate is Tunnel cutover, `Start`, and the remote connector contract against `phase5-clean`.

## Completion criteria

Packaging Phase 5 is PASS only when all of the following are recorded:

- exact installer SHA-256 verified;
- clean-machine install succeeded;
- no Python/uv/pwsh dependency is visible on the isolated product runtime `PATH`;
- worker mode is `local`;
- Git prerequisite is resolved and reported by `doctor`;
- Profile A stores the Cloudflare tunnel token with DPAPI after the plaintext environment value is removed;
- Profile B, when exercised, retains its OAuth verification-secret DPAPI behavior;
- bundled `cloudflared` is found (and the optional `tunnel-client` remains packaged);
- project registration succeeds;
- Bridge and Tunnel are owned and healthy;
- the Profile A remote ChatGPT Connector contract succeeds against the disposable project, or the Profile B contract is explicitly recorded when that profile is selected;
- ordinary public traffic is blocked by the Cloudflare WAF before Tunnel/Bridge ingress;
- Connector traffic is visible as an allowed Cloudflare network event;
- final Git state returns to the recorded baseline and is clean;
- cleanup/uninstall succeeds;
- no Phase H live action or `v0.1.0` freeze is implied by local gates.

Phase H live acceptance is now complete for the working `codemcp-557` Profile A path: real ChatGPT No-Auth access, complete tool discovery, project access, mutation, identical replay, explicit approval, checkpoint/CAS restore, exact clean-baseline recovery, ordinary-source Cloudflare `Block`, and ChatGPT-source `Allow` were recorded. The final registered gates also passed (`316 passed, 6 skipped`; 72 files already formatted). Strict Packaging Phase 5 PASS is intentionally not claimed because the live mutation used a dedicated temporary file in the registered `codemcp-remote` repository rather than the disposable `phase5-clean` repository, and Cleanup/uninstall was deferred to preserve the validated working connector. The optional stopped-Tunnel `1033` proof was also skipped. These are documented Phase H/Packaging Phase 5 deviations. They do not block normal use of the validated `codemcp-557` network-trust path, but **stable `v0.1.0` remains blocked by the mandatory repository-wide Phase 7 gates in `docs/acceptance/acceptance-test-plan.md`**; this document does not imply that a release tag or artifact has been approved.
