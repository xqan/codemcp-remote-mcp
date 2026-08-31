# codemcp-remote v0.1.0 macOS Dual-Architecture CLI Implementation Plan

[Simplified Chinese](zh-CN/implementation-plan.md)

> Updated: 2026-08-31  
> Implementation branch: `codex/macos-cli-packaging`  
> Status: **PHASE 1 COMPLETED / PHASE 2 COMPLETED / PHASE 3 GITHUB NATIVE GATE PASS / PHASE 4 INTEL64 LIVE GATE PASS / ARM64 REAL-HOST GATE PENDING / RELEASE BLOCKED**  
> Previous frozen baseline: [`plans/v0.1.0/windows-release-baseline-2026-08-28.md`](plans/v0.1.0/windows-release-baseline-2026-08-28.md)

This plan is the English canonical implementation record for the additive macOS dual-architecture work. Intel64 live acceptance is complete, but macOS support and the combined `v0.1.0` release remain blocked until the required Apple Silicon real-host gate is complete.

## Goal

Add two native macOS CLI release artifacts to the `v0.1.0` release family:

```text
codemcp-remote-v0.1.0-macos-arm64.tar.gz
codemcp-remote-v0.1.0-macos-intel64.tar.gz
```

Each archive must contain one visible top-level directory:

```text
codemcp-remote/
├── codemcp-remote
├── codemcp-install.sh
├── codemcp-start.sh
├── codemcp-stop.sh
├── config/
├── LICENSE
├── THIRD_PARTY/
├── SHA256SUMS.txt
└── BUILD_PROVENANCE.json
```

The PyInstaller `onedir` runtime may additionally use one hidden internal implementation directory:

```text
codemcp-remote/.codemcp-runtime/
```

That directory is not a public configuration surface and must not contain writable runtime state, user configuration, or secrets.

## Definition of done

The macOS track is complete only when all of the following are true:

- `macos-arm64` is a native thin `arm64` build;
- `macos-intel64` is a native thin `x86_64` build;
- installed execution does not require Python, `uv`, PowerShell, or Homebrew;
- Git remains the explicit runtime prerequisite;
- `./codemcp-install.sh` performs an interactive first-time setup through the official CLI;
- the existing 22-tool MCP contract, security policy, Git checkpoints, CAS restore, and ChatGPT-only reasoning boundary are preserved;
- `init`, `project`, `start`, `status`, `stop`, and `doctor` work on both architectures;
- persistent secrets use macOS Keychain and never fall back to plaintext;
- release artifacts are ad-hoc signed and explicitly **not notarized** because no Developer ID certificate is available;
- provenance, checksums, third-party notices, and supply-chain inputs are auditable;
- real Apple Silicon and real Intel clean-host acceptance is complete;
- shared changes pass the required Windows regression gates.

macOS packages include only `cloudflared` as the supported packaged transport. OpenAI `tunnel-client` remains in the product as an existing optional compatibility provider but is not part of the macOS release payload or acceptance contract.

## Current verified facts

The implementation is based on these repository facts:

1. `bridge/pyproject.toml` defines the Python 3.12+ package, version `0.1.0`, and the CLI entry points.
2. The frozen executable entry point can be shared across Windows and macOS.
3. The lifecycle self-spawns the Bridge and transport processes.
4. The worker path supports native POSIX execution; Windows compatibility patches are conditional.
5. Windows uses DPAPI; macOS uses a platform-specific Keychain secret store.
6. POSIX lifecycle ownership requires process-group and stable process-start identity checks.
7. `cloudflared` discovery supports a packaged exact path before PATH fallback.
8. Release builds require a clean worktree, exact source identity, pinned build tools, provenance, licenses, and SHA-256 evidence.
9. The macOS release workflow produces native candidates on separate GitHub-hosted architectures.
10. Existing Windows release evidence does not automatically prove the additive macOS release target.

## Architecture decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Artifacts | Two native thin builds: `arm64` and `x86_64` | No Rosetta or cross-architecture ambiguity |
| Freeze mode | PyInstaller `onedir` with `.codemcp-runtime` | Preserves stable self-spawn and process ownership semantics |
| Writable home | macOS packaged default: `~/Library/Application Support/codemcp-remote` | Keeps the distribution relocatable and read-only |
| Secret storage | Environment first, Windows DPAPI, macOS Keychain, no plaintext fallback | Fail-closed platform storage |
| POSIX lifecycle | New session/process group plus stable start marker | Prevents PID-reuse and foreign-process termination |
| Transport | Bundle pinned `cloudflared` only | Matches the macOS release scope |
| Interactive setup | Shell script orchestrates the official CLI | Avoids a second TOML/config implementation |
| Supply chain | Pin URL/version/SHA-256, validate safe extraction, preserve provenance | Reproducible and auditable release input |
| Signing | Ad-hoc only; no notarization | Matches the no-certificate constraint truthfully |
| Release scope | Add macOS to `v0.1.0` without changing the Windows contract | Shared changes still require Windows regression |

## Non-negotiable constraints

- Exact artifact names are fixed to the two names listed above.
- Archives contain exactly one top-level `codemcp-remote/` directory.
- `.codemcp-runtime/` is the only hidden runtime directory allowed by the packaging contract.
- `codemcp-install.sh` runs without `sudo` and does not install into system directories.
- The interactive setup is Cloudflare-only:
  - transport: `cloudflare`;
  - auth mode: `none`;
  - network trust: `cloudflare-chatgpt`.
- The setup script never calls Cloudflare APIs and never creates Tunnel, DNS, WAF, or IP List resources.
- `arm64` packages contain only `arm64` Mach-O slices; Intel packages contain only `x86_64`.
- Rosetta builds, cross-builds, `lipo` merging, and slice fabrication are forbidden.
- Candidate builds come from a clean exact commit.
- Runtime execution does not depend on Python, `uv`, PowerShell, Homebrew, or the source tree.
- Runtime state, user paths, logs, SQLite files, PID files, configuration, tokens, or Keychain exports must never enter the release payload.
- `SHA256SUMS.txt` covers every regular payload file except itself.
- `BUILD_PROVENANCE.json` contains source/build/tool/signing/input identity but no secret.
- macOS Keychain failure is explicit and fail-closed.
- The 22-tool MCP schema, database schema, idempotency, checkpoint, CAS, and security contracts are not widened.
- Windows DPAPI and the existing Windows packaged behavior remain compatible.
- Each implementation phase is independently verified before advancing.

## Phase 1 — macOS runtime and lifecycle security baseline

**Status: COMPLETED**

### Scope

- separate distribution root, bundled runtime root, and writable runtime home;
- add platform-aware `SecretStore`;
- keep environment secrets highest priority;
- preserve Windows DPAPI;
- add macOS Keychain storage;
- report real secret source labels;
- introduce POSIX process group ownership;
- record PID, PGID, executable identity, and a stable start marker;
- fail closed on PID reuse, marker mismatch, invalid PGID, or foreign ownership;
- prefer packaged `cloudflared` before legacy/PATH discovery.

### Security requirements

- no plaintext secret fallback;
- no secret in argv, generated TOML, logs, or temporary files;
- no signal to an unverified process group;
- old PID-only POSIX state is never trusted for termination;
- shared lifecycle changes must preserve Windows behavior.

### Validation

Required automated validation includes lock consistency, Ruff, targeted lifecycle/entry-point tests, and the full Bridge/integration suite. Native macOS smoke additionally covers Keychain round-trip and process-group ownership.

## Phase 2 — dual-architecture build and supply-chain implementation

**Status: IMPLEMENTED**

### Scope

- `scripts/build-macos-release.sh`;
- verified release-asset helper and pinned manifest;
- `codemcp-install.sh`, `codemcp-start.sh`, and `codemcp-stop.sh`;
- macOS packaging and executable-host tests;
- deterministic `onedir` assembly;
- bundled pinned `cloudflared`;
- Mach-O architecture validation;
- ad-hoc signing;
- `BUILD_PROVENANCE.json`;
- `SHA256SUMS.txt`;
- third-party license evidence.

### Build rules

The builder must verify:

- Darwin host;
- native machine architecture equals requested target architecture;
- clean Git worktree;
- exact source/version identity;
- pinned Python/uv/PyInstaller toolchain;
- pinned external assets and SHA-256;
- safe archive extraction;
- no unexpected executable or unsafe path entry.

The authoritative dual-architecture release candidates are not local developer builds. They are generated in Phase 3 on the corresponding native GitHub-hosted runners from the same source commit.

### Interactive installer contract

`codemcp-install.sh` must:

1. require a real TTY;
2. verify internal checksums before reading a secret;
3. explain that Cloudflare account-side resources are external prerequisites;
4. collect public MCP URL, exact allowed host, optional allowed origin, and optional first project;
5. read the Tunnel token with terminal echo disabled;
6. keep the token out of argv, files, logs, and output;
7. call the official `codemcp-remote init` and `project add` commands with quoted arguments;
8. refuse unsafe overwrite of an existing home;
9. run `doctor` after successful initialization;
10. never auto-start a public connection after a failed setup.

## Phase 3 — native CI, ad-hoc signing, and candidate convergence

**Status: GITHUB NATIVE GATE PASS**

The authoritative release workflow uses native jobs:

| Candidate | Runner | Architecture |
| --- | --- | --- |
| `macos-arm64` | `macos-15` | `arm64` |
| `macos-intel64` | `macos-15-intel` | `x86_64` |

Both native jobs must:

- build from the same clean source identity;
- verify all Mach-O slices;
- use ad-hoc signing;
- prove that no project Developer ID identity is present;
- record `developer_id=false`;
- record notarization as `not_performed` with reason `no_certificate`;
- preserve candidate SHA-256 and validation evidence;
- avoid signing/notary secrets entirely.

A convergence job rejects the run unless source identity, version, provenance schema, tool versions, lock hash, and signing policy match across both candidate artifacts.

The repository validation ledger currently records the Phase 3 gate as PASS, while exact retained workflow artifact metadata still needs to remain available for final release signoff.

## Phase 4 — clean-host acceptance and final release gate

**Status: INTEL64 LIVE GATE PASS / ARM64 REAL-HOST ACCEPTANCE PENDING / RELEASE BLOCKED**

### Current Phase 4 repository state — 2026-08-31

The current repository state, not chat history, records the following:

- `scripts/validate-clean-macos-release.sh` is implemented with `prepare`, `verify`, `secret-scan`, `lifecycle`, and `cleanup` actions, and the macOS build/install guide contains the clean-host flow.
- The `BridgeError` dataclass constructor failure was fixed by replacing zero-argument `super()` with explicit `Exception.__init__`; the raw `super(type, obj)` failure no longer reproduces on the deployed Intel64 runtime.
- Standard MCP `ToolAnnotations` are published for effectful tools. Live ChatGPT-host compatibility is verified for `checkpoint_create`, `approval_confirm`, `checkpoint_restore`, `registered_command_run`, `test_run`, and `format_run` without weakening Bridge-side approval, CAS, checkpoint, audit, or fail-closed enforcement.
- Intel64 live acceptance passed for project open/read/write, automatic commits/checkpoints/diff, one-time approval behavior, checkpoint restore, stale-HEAD CAS rejection, project-registry hot reload, and deterministic registered `verify`, `test`, and `format` commands.
- `scripts/codemcp-install.sh` now removes `com.apple.quarantine` recursively from its extracted distribution directory on Darwin before continuing setup. This is a best-effort unsigned-candidate usability measure; it does not modify packaged file bytes, and release guidance must still preserve trusted archive digest verification.
- Fresh Intel64 real-machine quarantine acceptance is PASS: `./codemcp-install.sh` proceeded without any manual `xattr -dr` step and without the previous bundled Python-extension Gatekeeper block.
- The final repository regression after all of the above changes is **393 passed, 0 failed, 8 skipped, 1 warning**. The former two real-codemcp failures were traced to a stale Phase 2 test fixture that forced WSL2 even though the current release contract is `adapter_mode="native-stdio"` with `worker_mode="local"`; after aligning the integration fixture with the release default, the full suite passed.
- Intel64 final release gate is PASS for the unsigned candidate path.
- Apple Silicon real-host clean-machine acceptance remains pending. GitHub native arm64 build evidence does not replace this required real-host gate.
- The combined macOS / `v0.1.0` release gate remains blocked only on the required ARM64 real-host evidence and any final documentation/release signoff that depends on it.

Phase 4 validates the Phase 3 artifacts on:

- a real Intel Mac;
- a real Apple Silicon Mac;
- a Windows 11 host for shared-regression evidence.

### Required macOS acceptance

For each architecture:

1. verify the outer archive SHA-256 from a trusted release channel before running any extracted code;
2. verify internal `SHA256SUMS.txt` as part of the installer/distribution integrity flow;
3. retain truthful provenance that the candidate is ad-hoc signed and not notarized;
4. run `codemcp-install.sh`; on Darwin the installer best-effort clears `com.apple.quarantine` recursively from its own extracted distribution before continuing, without modifying packaged file bytes;
5. verify that setup does not require a separate manual `xattr -dr` step on accepted unsigned-candidate hosts;
6. verify Keychain secret persistence;
7. register and operate on a disposable Git repository;
8. exercise read, mutation, replay, checkpoint, and restore;
9. run at least 20 start/status/stop cycles;
10. test Bridge/Tunnel/worker crash and process cleanup;
11. test stale/reused PID and process ownership protection;
12. test unknown/reconcile behavior;
13. verify isolated PATH operation without Python/uv/pwsh/Homebrew;
14. cover spaces, Unicode paths, long paths, symlink escape, and read-only distribution behavior;
15. leave no secret, log, process, or acceptance-project residue after cleanup.

### Final documentation gate

Before support is declared, all of these must agree with the actual evidence:

- root README;
- architecture/security/threat model;
- operations and platform guides;
- macOS validation ledger;
- final release acceptance plan;
- changelog/open-source readiness records.

Historical reports are not rewritten to simulate current evidence.

## Final validation checklist

The release evidence must prove:

1. **Source identity** — exact source commit/tag, clean tree, lock hash.
2. **Architecture** — every Mach-O matches the target thin architecture.
3. **Artifact shape** — one top-level directory, stable visible files, one internal runtime directory.
4. **Integrity** — external archive SHA-256 plus internal file checksums.
5. **Signing** — valid ad-hoc signatures, no Developer ID, no notarization, documented quarantine behavior.
6. **Interactive setup** — safe TTY input, Keychain storage, repeat/cancel/failure handling.
7. **Runtime independence** — no Python/uv/pwsh/Homebrew dependency; Git remains explicit.
8. **Lifecycle/security** — process ownership, crash handling, timeout, unknown/reconcile, redaction, loopback/network trust.
9. **Functional contract** — frozen worker and all 22 public MCP tools preserve authorization/mutation/recovery semantics.
10. **Cross-platform regression** — Windows packaged/runtime behavior and source CI remain healthy.
11. **Supply chain/legal** — pinned inputs, dependency audit, license inventory, notices, and corresponding source are complete.

Any later change to runtime code, the lockfile, external binaries, signing, or artifact assembly invalidates the affected evidence and requires the corresponding gate to be rerun.

## Open risks

| Risk | Current state | Rule |
| --- | --- | --- |
| No Developer ID / notarization | Accepted product limitation | Publish only ad-hoc, non-notarized artifacts; never claim Apple trust |
| Gatekeeper quarantine | Intel64 installer auto-cleanup PASS; unsigned limitation remains | Verify trusted archive digest before running extracted code; installer cleanup is a usability measure, not Apple notarization/trust |
| Apple Silicon clean-host resource | Pending | CI candidate does not replace real-host acceptance |
| Intel clean-host resource | Final unsigned-candidate live gate PASS | Preserve the recorded Intel64 evidence; rerun if runtime, packaging, signing, or installer behavior changes materially |
| Minimum macOS version | Not yet proven | Claim only versions with real evidence |
| Keychain ACL/upgrade behavior | Intel64 base live path accepted; ARM64/upgrade-relocation matrix still pending | Fail closed on denial or backend mismatch |
| macOS `codemcp==0.3.0` runtime behavior | Intel64 real project read/write/checkpoint/restore/commands PASS; ARM64 pending | Never silently replace the backend |
| Shared Windows/source regression | Current full repository suite PASS at 393/0/8; mandatory after affected changes | macOS changes cannot weaken the accepted Windows contract |

## Current handoff

Do not restart completed Intel64 work. The repository and real Intel Mac have already proven the current implementation through the final unsigned-candidate Intel64 gate.

Continue Phase 4 from this exact sequence:

1. preserve the current green repository baseline: **393 passed, 0 failed, 8 skipped, 1 warning**;
2. preserve the Intel64 evidence for approval/restore/CAS, registered commands, project-registry hot reload, quarantine auto-cleanup, and the final unsigned-candidate live gate;
3. build/retain the matching arm64 candidate from the same release contract and confirm its CI/native packaging evidence remains green;
4. run the equivalent clean-host evidence chain on a real Apple Silicon Mac, including installer/quarantine behavior, Keychain, read/write/checkpoint/restore, registered commands, lifecycle/security, and cleanup;
5. update the acceptance ledger, this English canonical plan, the independent Chinese plan, and `docs/development-state.md` with the ARM64 result;
6. only when the required ARM64 real-host gate and final documentation/release signoff are complete may the combined macOS / `v0.1.0` release gate be marked PASS.

The old `BridgeError.__post_init__` and two-failure WSL2 integration baselines are closed; they must not be carried forward as accepted defects. Any later runtime, packaging, signing, external-binary, installer, or lockfile change invalidates the affected evidence and requires the corresponding gate to be rerun.

Use [`acceptance/macos-v0.1.0-validation.md`](acceptance/macos-v0.1.0-validation.md), current Git state, tests, `docs/development-state.md`, this English canonical plan, and the independent Chinese plan as repository sources of truth. Critical status must be written back to all relevant repository documents; chat history is not authoritative.
