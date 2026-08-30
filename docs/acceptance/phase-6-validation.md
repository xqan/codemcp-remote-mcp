# Phase 6 Validation — Windows 11 Operations

> Status: **PASS / COMPLETE — FINAL RC MANDATORY PHASE 6 MATRIX CLOSED**  
> Updated: 2026-08-30  
> Release target: `v0.1.0`

## 1. Goal

Validate that the **current packaged Windows release path** can be started, diagnosed, stopped, recovered and operated predictably on Windows 11.

The mandatory `v0.1.0` Phase 6 profile is:

```text
Windows 11
  + packaged codemcp-remote.exe
  + Git for Windows
  + Native Windows local worker
  + Cloudflare Tunnel
  + Profile A: auth.mode=none
  + network_trust.mode=cloudflare-chatgpt
```

WSL2 and OpenAI Secure MCP Tunnel remain compatibility paths. Their existence must not make WSL2, Python, `uv`, PowerShell 7 or `CONTROL_PLANE_API_KEY` a mandatory installed-runtime requirement.

A documented case is not PASS until real-host evidence is captured.

## 2. Mandatory validation environment

Use the final or current release-candidate artifact on a supported Windows 11 x64-compatible host/VM.

Required:

- Windows 11;
- installed `codemcp-remote.exe`;
- Git for Windows;
- Native Windows `worker_mode=local`;
- bundled `cloudflared`;
- configured Cloudflare Tunnel;
- Profile A network trust;
- Bridge bound to loopback;
- a disposable registered Git project;
- no dependency on source checkout for normal product execution.

The installed runtime must continue to work when these development tools are absent from the product runtime `PATH`:

- Python;
- `uv`;
- PowerShell 7;
- WSL2 worker environment.

Source-development validation may still use Python/uv/pwsh and the repository scripts, but that is supporting evidence rather than the mandatory packaged-runtime contract.

## 3. Evidence handling

Acceptance evidence must not contain real credentials.

Use synthetic canaries and redact:

- Cloudflare `TUNNEL_TOKEN`;
- OAuth Resource Server verification secret when Profile B is separately tested;
- `CONTROL_PLANE_API_KEY` when the optional Secure MCP compatibility transport is tested;
- Bearer tokens;
- approval tokens;
- private-key or secret-file contents.

Evidence may contain local paths, process IDs and Git metadata. Treat it as sensitive operational data even after redaction.

Do not commit runtime logs, SQLite databases, DPAPI secret blobs, local project registry values or acceptance credentials.

### 3.1 Current repository-side pre-RC evidence

The repository-side gate is green on runtime-code commit `01dc912a15134abd78aa5a7487e716e691005092` (the latest runtime-code commit before this acceptance-document update):

```text
format:
79 files already formatted

complete registered test workflow:
353 passed, 7 skipped, 2 warnings

security-audit:
dependency_audit = passed
dependency_license_evidence = passed
current_tree_secret_scan = passed
git_history_secret_scan = passed
artifact_scan = not-run
dependency_license_compatibility_review = manual-required
```

The two pytest warnings are non-failing environment/tooling warnings. This code baseline includes symmetric partial recovery for both `Bridge healthy / Tunnel dead` and `Bridge dead / Tunnel healthy`, plus regression coverage for both directions. It was the repository-side baseline before the final packaged Phase 6 execution and does **not** by itself constitute the Phase 6 PASS evidence.

### 3.2 Final Phase 6 closure synchronization

The status text in Sections 4–8 below preserves the chronology of earlier/superseded RC attempts. Those historical `NEXT-RC RERUN REQUIRED` / `PENDING` labels are **not the current release status**. The current Live Acceptance Ledger in `docs/plans/v0.1.0/open-source-readiness-plan.md` records the final Windows 11 mandatory real-host/fault/path/log matrix as **PASS / COMPLETE**. Section 10 is the current Phase 6 exit record.

A later source/runtime/artifact change that invalidates this evidence must reopen the affected Phase 6 cases. The final release-only artifact rebuild remains a separate Final Release Gate requirement and does not, by itself, rewrite the historical execution notes below.

## 4. A — Repeatable packaged lifecycle

### 4.1 Mandatory 20-cycle gate

From the installed release candidate, execute at least 20 complete cycles:

```text
codemcp-remote.exe start
  -> codemcp-remote.exe doctor
  -> codemcp-remote.exe status
  -> codemcp-remote.exe stop
```

Each iteration must prove:

- start exits successfully;
- Bridge becomes healthy;
- Cloudflare Tunnel becomes healthy/ready;
- `doctor` reports the configured Profile A contract;
- `worker_mode = local`;
- Git prerequisite is available;
- stop terminates only product-owned process trees;
- no owned Bridge/Tunnel/worker process is left behind;
- unrelated listeners/processes are not killed;
- the next iteration starts from a known state.

Required release result:

```text
requested_iterations = 20
completed_iterations = 20
failed_iterations = 0
```

Status: **PASS ON SUPERSEDED RC `c18f0a0fc8c2f0250b486f9d7e556dc76fefb792`; MUST RERUN ON THE NEXT RC AFTER THE SYMMETRIC LIFECYCLE RECOVERY FIX.**

### 4.2 Packaged Phase 6 local-host harness

The final RC carries:

```powershell
pwsh -File .\scripts\validate-phase6-windows.ps1 -Iterations 20
```

This harness is intentionally bounded to cases that can be proven locally on the clean Windows acceptance host. It verifies:

- 20 complete packaged `start -> doctor -> status -> stop` cycles;
- Profile A `auth.mode=none`, `network_trust.mode=cloudflare-chatgpt`, `identity_level=network-only`;
- Native Windows `worker_mode=local`;
- Git availability and Git-missing diagnostics;
- DPAPI Cloudflare Tunnel token recovery and missing-token diagnostics;
- Bridge abnormal exit detection and recovery;
- Tunnel abnormal exit detection and recovery;
- unrelated Bridge-port occupancy fail-safe behavior;
- unrelated Tunnel metrics-port occupancy fail-safe behavior;
- plaintext credential-shape scanning across product logs;
- final runtime stop with no acceptance runtime intentionally left running.

On success it returns `status = phase6-local-host-gate-pass`, but it also emits `remaining_remote_cases` and explicitly refuses to claim full Phase 6 PASS.

The remaining cases are:

- native worker abnormal exit during a controlled mutation;
- Tunnel disconnect during mutation;
- restart around the backend mutation boundary plus `unknown` reconciliation;
- registered-command timeout / child-process-tree handling;
- Windows path and encoding matrix through the public MCP contract.

Status: **HARNESS EXECUTED 20/20 ON SUPERSEDED RC; NEXT-RC CLEAN-HOST RERUN REQUIRED.**

### 4.3 Source-mode lifecycle runner

The existing source-development runner remains useful:

```powershell
pwsh -File .\scripts\validate-lifecycle.ps1 -Iterations 20
```

It validates the repository PowerShell lifecycle path and stores local evidence under `.local/validation/`.

This runner does **not** replace the mandatory packaged-runtime 20-cycle gate.

### 4.3 Non-destructive live-host smoke

`tests/integration/test_phase6_live_host.py` provides a bounded Windows-only smoke that can reuse an already healthy loopback Bridge without stopping it. When `http://127.0.0.1:46200/healthz` is reachable from the registered test process, it executes:

- `scripts/doctor.ps1 -SkipTunnel`;
- `scripts/stop-all.ps1 -WhatIf`.

The smoke never performs an actual stop. If no live Bridge is visible from the test process, it skips rather than pretending that live-host evidence exists.

Current evidence on 2026-08-28:

```text
331 passed, 7 skipped
Phase 6 live-host smoke: SKIPPED
reason: no live loopback Bridge is available on the Phase 6 baseline port
```

This confirms that the current `codemcp-557` control path cannot be counted as same-process/same-port source-mode Phase 6 evidence merely because remote MCP calls are working. The mandatory packaged-runtime 20-cycle gate remains PENDING and must run in an isolated acceptance lifecycle where stopping the candidate cannot terminate the control channel used to conduct the test.

## 5. B — Failure and recovery matrix

Every case starts from a known healthy packaged Profile A baseline.

| Case | Injection / setup | Required outcome | Status |
|---|---|---|---|
| Bridge exits unexpectedly | terminate the product-owned Bridge process | `doctor` identifies Bridge failure; restart restores health; no mutation is silently replayed | LOCAL-HOST ABNORMAL-EXIT PASS ON SUPERSEDED RC; BOUNDARY-RECOVERY BLOCKER FIXED IN SOURCE; NEXT-RC RERUN REQUIRED |
| `cloudflared` exits unexpectedly | terminate the product-owned Cloudflare Tunnel process | Bridge remains loopback-local; remote readiness fails; restart restores transport without mutation replay | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Native codemcp worker exits unexpectedly | terminate active local worker during a controlled operation | failure/uncertainty is surfaced; uncertain mutation becomes `unknown`; unsafe follow-up mutation remains blocked pending reconcile | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Bridge port occupied | bind Bridge port with unrelated process | product refuses to adopt/kill unrelated listener and fails safely | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Tunnel health/control port occupied | bind relevant local transport port with unrelated process | startup/stop does not kill unrelated process; actionable state is reported | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| stale runtime/process metadata | simulate recoverable stale owned state | product replaces/cleans only state it can prove it owns | SOURCE COVERAGE EXPANDED; NEXT-RC REAL-HOST RERUN REQUIRED |
| Git unavailable | isolate product PATH without Git | `doctor`/start fails with actionable Git prerequisite error; no mutation dispatch | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Cloudflare token missing/invalid | isolated Profile A with absent/invalid transport credential | Tunnel readiness fails without credential disclosure; local Bridge remains diagnosable | MISSING-SECRET DIAGNOSTIC PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Tunnel disconnect during mutation | controlled transport interruption at backend boundary | operation is not transparently replayed; uncertainty is reconciled explicitly | PASS ON SUPERSEDED RC: Bridge PID preserved, Tunnel-only recovery succeeded, same client request returned the persisted operation/checkpoint without duplicate execution; NEXT-RC RERUN REQUIRED |
| command timeout/process tree | registered bounded fixture spawns child and exceeds timeout | owned process tree terminates or outcome becomes explicitly unknown/fail-closed | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| restart with pending approval | restart while operation awaits approval | stale plaintext approval is unavailable; operation/session recovery follows defined fail-closed semantics | PENDING |
| restart around backend boundary | restart before and after dispatch boundary | pre-dispatch operation fails safely; uncertain post-boundary mutation is `unknown` | RELEASE BLOCKER REPRODUCED ON SUPERSEDED RC; SYMMETRIC RECOVERY FIXED IN SOURCE; NEXT-RC RERUN REQUIRED |

Clean-machine boundary evidence reproduced the missing symmetric lifecycle case: after the approval-required `phase6-boundary` command had entered execution, the Bridge process tree was terminated while the Tunnel remained product-owned and healthy. Status correctly reported `degraded`, Bridge `owned=false`/unreachable, and Tunnel `owned=true`/healthy, but `Action Start` failed with `Tunnel health endpoint is already occupied; refusing unsafe takeover`. The source fix now reuses an owned healthy Tunnel and starts only the missing Bridge, mirroring the already-validated Tunnel-only recovery path. Full regression on the fixed runtime code is `353 passed, 7 skipped, 2 warnings`.

Destructive fault injection must use a disposable fixture repository or dedicated acceptance project.

## 6. C — Log and credential canary validation

Use synthetic values only.

Mandatory Profile A canaries:

- `TUNNEL_TOKEN=<synthetic value>`;
- `Authorization: Bearer <synthetic value>`;
- an `sk-...` shaped synthetic key;
- approval-token-shaped text;
- a synthetic denied secret file.

When optional compatibility profiles are tested, also include:

- `CONTROL_PLANE_API_KEY=<synthetic value>`;
- Profile B Resource Server verification-secret canary.

Inspect:

- Bridge logs;
- Cloudflare Tunnel logs;
- worker stderr logs;
- lifecycle/validation evidence;
- `doctor`/status output;
- crash/recovery diagnostics.

Release requirement:

- credential canaries are absent or redacted;
- plaintext approval tokens are not persisted;
- denied secret-file contents do not appear in unrestricted diagnostics;
- useful error context remains after redaction.

The packaged local-host harness on superseded RC `c18f0a0fc8c2f0250b486f9d7e556dc76fefb792` completed the plaintext credential-shape scan with `log_finding_count=0`. Repository security audit on the fixed runtime code also reports no current-tree or Git-history leaks. The next RC must repeat the packaged canary scan before final release.

Status: **PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED.**

## 7. D — Encoding and Windows path matrix

Run representative registered fixture projects with Native Windows worker mode.

| Case | Required outcome | Status |
|---|---|---|
| ASCII path | normal read/mutation/test/Git flow | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| path containing spaces | same security and mutation behavior as ASCII | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| Chinese path / filename | correct UTF-8 read/write/search/log/Git behavior | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| CRLF source file | exact edit/write preserves intended line-ending semantics | PARTIAL PASS ON SUPERSEDED RC; NEXT-RC EXACT EDIT/WRITE RERUN REQUIRED |
| LF source file | exact edit/write preserves intended line-ending semantics | PARTIAL PASS ON SUPERSEDED RC; NEXT-RC EXACT EDIT/WRITE RERUN REQUIRED |
| supported long Windows path | success or explicit supported-limit failure; no silent truncation | PASS ON SUPERSEDED RC; NEXT-RC RERUN REQUIRED |
| mixed command output encoding | output remains bounded/diagnosable without secret disclosure | PENDING |

WSL path mapping is a separate compatibility test when `worker_mode=wsl2` is intentionally advertised; it is not part of the mandatory Native Windows installed-runtime matrix.

## 8. E — Dependency upgrade and rollback

Before changing `codemcp`, MCP SDK or another execution-path dependency:

1. record the known-good dependency lock and release-candidate commit;
2. preserve the existing lock file;
3. change one dependency scope at a time;
4. run `doctor`;
5. run the complete automated suite;
6. run the codemcp compatibility matrix;
7. rerun affected Phase 6 lifecycle/recovery cases;
8. rerun Phase 7 security acceptance;
9. compare the exposed 22-tool MCP schema;
10. reject the upgrade if mutation/Git/process/replay semantics change without an explicit design review.

Rollback:

1. stop all product-owned Bridge/Tunnel/worker trees;
2. restore known-good dependency metadata and lock;
3. rebuild the release candidate from the known-good commit;
4. run `doctor`;
5. run the complete automated suite;
6. run at least one packaged lifecycle cycle;
7. reconcile any pre-existing `unknown` mutation separately;
8. do not treat dependency rollback as proof that an uncertain repository mutation was reverted.

Status: **DOCUMENTED; FINAL RELEASE-BASELINE REVIEW PENDING.**

## 9. Compatibility-only checks

These are not mandatory default-runtime dependencies, but must remain truthful if advertised:

### WSL2 fallback

When explicitly configuring `worker_mode=wsl2`:

- missing/unavailable WSL produces an actionable diagnostic;
- no mutation dispatch occurs when worker preparation is invalid;
- path mapping and worker stderr remain bounded and correct.

### OpenAI Secure MCP Tunnel

When explicitly selecting the compatibility transport:

- loopback target constraints remain enforced;
- plaintext `CONTROL_PLANE_API_KEY` is not persisted in repo/profile/logs;
- transport failure does not grant broader Bridge authorization.

### OAuth Profile B

When explicitly testing Profile B:

- its verification secret uses the documented protected source;
- subject/client/scope identity remains separate from Profile A `network-only` identity.

A failure in an advertised compatibility feature must either be fixed or documented by narrowing the `v0.1.0` support claim.

## 10. Release exit criteria

The final Phase 6 closure is synchronized to the current Live Acceptance Ledger. All mandatory Phase 6 items are closed:

- [x] 20/20 packaged lifecycle iterations PASS;
- [x] Bridge, Cloudflare Tunnel and Native Windows worker abnormal-exit cases PASS;
- [x] unrelated port/listener cases fail safely;
- [x] Git and transport-credential dependency failures produce actionable diagnostics;
- [x] restart/backend-boundary recovery follows `failed`/`unknown` semantics;
- [x] timeout/process-tree cleanup PASS;
- [x] synthetic log/credential canary scan PASS;
- [x] spaces, Chinese paths, line endings and supported long paths PASS;
- [x] dependency upgrade/rollback procedure reviewed against current pinned baseline;
- [x] no Phase 6 P0/P1 blocker remains open.

Current Phase 6 decision:

```text
PASS / COMPLETE
```

Stable `v0.1.0` remains blocked only by the later Final Release Gate items recorded in the active open-source readiness plan.
