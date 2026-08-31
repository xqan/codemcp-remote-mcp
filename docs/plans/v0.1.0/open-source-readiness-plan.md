# codemcp-remote Open Source Readiness Plan — v0.1.0

[Simplified Chinese](../../zh-CN/open-source-readiness-plan.md)

> Baseline updated: 2026-08-30  
> Target release: `v0.1.0`  
> Final public repository: `xqan/codemcp-remote-mcp`  
> Final public branch: `main`  
> Release-source freeze: this release-only documentation tree; the exact public tag target is frozen only after synchronization to public `main`  
> Runtime-code acceptance: approval hotfix and the complete clean-machine restore contract are PASS; runtime code must not change after the release-only freeze without invalidating affected evidence  
> Status: **RELEASE SOURCE FREEZE RECORD / POST-FREEZE ARTIFACT SIGN-OFF IS EXTERNAL TO THIS TREE**

This document is the current English canonical readiness plan. Historical execution details remain in the versioned acceptance and testing records. The Simplified Chinese version preserves the pre-i18n plan text for audit and operator continuity.

## 1. Purpose

The goal is to close `codemcp-remote` from a project that is already usable in a controlled personal environment into its first stable public open-source release: reviewable, safely installable, reproducibly testable, and maintainable.

This plan covers only release-readiness work for `v0.1.0`:

- security and threat-model closure;
- reliability and recovery;
- public documentation and onboarding;
- GitHub governance;
- secrets/privacy/supply-chain review;
- deterministic packaging and release identity;
- final clean-machine and publication gates.

Completed product capabilities are not reclassified as unfinished implementation work.

A stable tag must be justified by evidence bound to the final release candidate. “The code exists”, “it works locally”, or “an older candidate passed” is not a substitute for the final release gate.

## 2. Current product baseline

### 2.1 Installed Windows product

The `v0.1.0` installed-product baseline is:

- Windows 11 x64-compatible;
- packaged `codemcp-remote.exe` and `codemcp-remote-setup.exe`;
- **no Python, `uv`, PowerShell 7, or WSL2 runtime dependency**;
- Git for Windows is the explicit runtime prerequisite;
- the default mutation worker is the **native Windows local worker**;
- WSL2 Ubuntu remains an optional source-mode compatibility fallback;
- `codemcp==0.3.0` is pinned and used through a Bridge-owned Windows compatibility wrapper without maintaining an upstream fork.

Source development still requires Python 3.12+, `uv`, and PowerShell 7. Documentation must keep source-development prerequisites separate from installed-runtime prerequisites.

### 2.2 Recommended remote profile

The recommended personal `v0.1.0` Profile A is:

```text
ChatGPT Connector (Authentication = No authentication)
  -> OpenAI / ChatGPT Connector egress
  -> Cloudflare Edge / WAF IP allowlist
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200
  -> codemcp-remote Bridge
  -> native Windows codemcp worker
  -> registered local Git project
```

Security meaning:

- the Cloudflare WAF/IP allowlist is a **network trust boundary**, not user authentication;
- Profile A reports `identity_level = network-only`;
- it must never claim to identify a specific ChatGPT user, Workspace, account, or conversation;
- the Bridge remains loopback-only;
- Cloudflare ingress policy is enforced externally, not by trusting forwarded client-IP headers in Python;
- OpenAI Secure MCP Tunnel remains an optional compatibility transport;
- Profile B `oauth-resource-server` remains an optional advanced/enterprise profile rather than the default personal path.

### 2.3 MCP and local safety contract

The public MCP contract contains 22 tools:

1. `project_open`
2. `project_status`
3. `file_read`
4. `code_search`
5. `file_list`
6. `file_edit`
7. `file_create`
8. `file_write`
9. `file_move`
10. `file_delete`
11. `directory_create`
12. `registered_command_run`
13. `format_run`
14. `test_run`
15. `git_status`
16. `git_diff`
17. `checkpoint_create`
18. `checkpoint_restore`
19. `operation_status`
20. `approval_confirm`
21. `operation_cancel`
22. `operation_reconcile`

The release must preserve these invariants:

- no arbitrary shell;
- no caller-controlled executable path or arbitrary argv;
- no arbitrary local absolute path bypassing the project registry;
- sensitive paths denied by default;
- canonical request hashing, idempotency, and per-project mutation serialization;
- short-lived one-time approval for high-risk operations;
- Bridge-owned Git checkpoints;
- branch/HEAD compare-and-swap restore;
- uncertain side effects remain `unknown` and require explicit reconciliation;
- the Bridge and codemcp do not call a model; ChatGPT is the only reasoning engine.

### 2.4 Project-authorization control plane

Project authorization is managed locally:

```text
local CLI project add/remove
  -> validated atomic projects.toml replacement
  -> running Bridge observes the validated registry change
  -> no Bridge / Tunnel / Connector restart required
```

Rules:

- the local CLI is the only normal project-authorization control plane;
- MCP does not expose project add/remove/reload/reconfigure;
- add/remove preserves root-ownership, last-known-good, revocation, and fail-closed semantics;
- direct `projects.toml` editing is reserved for trusted offline maintenance or recovery.

### 2.5 Completed evidence already available

Existing evidence includes:

- project license: GNU AGPL v3 / `AGPL-3.0-only`;
- `SECURITY.md`, security model, and threat model;
- public-user README and operator documentation;
- GitHub governance and CI configuration in the repository;
- Windows EXE, installer, release-candidate ZIP, and SHA-256 build flow;
- native Windows worker as the default execution path;
- project-registry hot reload with automated tests;
- Cloudflare No-Auth network-trust Phase A–H completion;
- live Phase H proof of:
  - ChatGPT Connector access to the complete 22-tool surface;
  - mutation;
  - identical replay;
  - explicit approval;
  - checkpoint / CAS restore;
  - exact clean-baseline recovery;
  - ordinary public source blocked by Cloudflare;
  - ChatGPT Connector source allowed.

Historical Phase H automation recorded `316 passed, 6 skipped` and `72 files already formatted`. Those are prior capability records, not substitutes for the final release-candidate gate.

## 3. Release-readiness status

| Stage / track | Status | Blocks stable `v0.1.0` | Meaning |
| --- | --- | ---: | --- |
| Stage 0 — Readiness baseline | **COMPLETE** | No | Open-source baseline established; release-only source tree content frozen, exact public tag target requires final synchronization |
| Stage 1 — License / Security / Threat Model | **COMPLETE** | No | Core legal/security docs and third-party notice strategy complete |
| Stage 2 — Phase 6 Windows Operations | **PASS / COMPLETE** | No | Mandatory Windows 11 real-host/fault/path/log matrix complete |
| Stage 3 — Phase 7 Final Acceptance | **SOURCE FREEZE COMPLETE / POST-FREEZE RELEASE SIGN-OFF EXTERNAL** | **Yes, before tag** | Functional, security, reliability, real-project, documentation, signing, and governance work closed on source; artifact sign-off remains external |
| Stage 4 — README / Onboarding | **DOCUMENTATION RELEASE BASELINE COMPLETE** | No | Public onboarding and limitations aligned; final package onboarding remains artifact evidence |
| Stage 5 — GitHub Governance / CI | **PASS / COMPLETE WITH HOSTED-CI WAIVER** | No | Public `main` protected; Dependabot/templates verified; hosted CI billing limitation is an accepted risk, not a PASS |
| Stage 6 — Secrets / Privacy / Supply Chain | **PASS WITH DOCUMENTED LICENSE DISCREPANCY / HOSTED-CI WAIVER** | No | Tree/history/artifact/dependency/license audits complete |
| Stage 7 — Release Packaging | **SOURCE FREEZE COMPLETE / FINAL ARTIFACT SIGN-OFF EXTERNAL** | **Yes, before tag** | Runtime hotfix artifact passed clean-machine contract; final public-commit artifact must be rebuilt and rebound |
| Network Trust Phase A–H | **COMPLETE** | No | Recommended Profile A live boundary complete |
| Optional OAuth Profile B | **IMPLEMENTED / OPTIONAL** | No* | Does not block while live OAuth E2E is not part of the default `v0.1.0` promise |

`*` If the release scope changes to promise full live OAuth interoperability, that scope must have its own acceptance evidence.

## 4. Current acceptance ledger

### 4.1 Completed release evidence

The source-freeze record includes:

- Phase 6 mandatory Windows real-host matrix: **PASS / COMPLETE**;
- Phase 7 functional F-01 through F-20: **PASS**;
- reliability/recovery R-01 through R-14: **PASS** using live and deterministic final-RC evidence;
- the security negative matrix is closed except environment-specific cases explicitly classified rather than hidden;
- symlink-escape deterministic tests passed; clean-machine live symlink creation may be environment-blocked by Windows privilege and is not treated as a product failure;
- expired approval returns `APPROVAL_EXPIRED` with unchanged Git state;
- cross-project operation/approval isolation is enforced;
- secret/log canary evidence passed;
- hidden model/provider egress check showed only expected loopback behavior for Bridge/native worker;
- network-trust Host/Origin/public-source/forwarded-header boundaries fail closed;
- working-tree, full Git-history, artifact, dependency, and license audit passed with the documented `codemcp==0.3.0` license-metadata discrepancy;
- final-RC registered regression recorded `354 passed, 7 skipped, 2 warnings`;
- final source format evidence recorded `80 files already formatted`;
- exact public MCP surface is 22 tools;
- the current RC installer/ZIP identity and artifact security audit were produced;
- GitHub-hosted CI billing/spending-limit behavior is recorded as **WAIVED / ACCEPTED RISK**, never as CI PASS;
- repository-prompt-injection acceptance passed and its disposable fixture was cleaned up;
- 10/10 complete real-project remote tasks passed: Java 5/5, frontend 3/3, recovery 2/2;
- the signing decision is **NotSigned / accepted limitation** with SmartScreen/reputation warnings explicitly disclosed;
- final GitHub governance is complete with the active public-main protection policy and Dependabot/templates evidence.

### 4.2 Source-freeze discipline

After source freeze:

1. do not modify the tag target merely to write back final commit hashes, artifact hashes, package sign-off, or publication results;
2. bind post-freeze evidence through `SOURCE_COMMIT.txt`, `BUILD_PROVENANCE.json`, `SHA256SUMS.txt`, artifact audit, clean-machine evidence, and the GitHub Release;
3. every PASS/FAIL/BLOCKED/ENVIRONMENT BLOCKED classification requires concrete evidence;
4. if a post-freeze finding requires runtime/security code changes, revoke the freeze, create a new source tree, and rerun every affected artifact/clean-machine gate;
5. final sign-off is based jointly on frozen source identity, final artifact identity, clean-machine evidence, and repository governance.

## 5. Stage requirements

### Stage 0 — Release boundary and baseline

**Status: COMPLETE**

The stable version is `v0.1.0`; the core architecture is frozen as ChatGPT-only reasoning, Bridge policy gateway, codemcp execution backend, native Windows default worker, and Cloudflare Profile A recommended remote path.

The final release candidate must always capture exact:

- branch and commit;
- clean worktree state;
- MCP tool contract;
- dependency lock;
- installer/package identity;
- test result.

### Stage 1 — License, security, and threat model

**Status: COMPLETE**

Completed:

- root `LICENSE`;
- GNU AGPL v3 / `AGPL-3.0-only`;
- matching package metadata;
- `SECURITY.md`;
- security model;
- threat model;
- mapping of important threats to automated or Phase 6/7 evidence;
- third-party dependency-license review;
- third-party notice strategy;
- explicit separation of project AGPL licensing from dependency licensing;
- engineering sign-off of the `codemcp==0.3.0` metadata MIT vs bundled Apache-2.0 License-File discrepancy.

### Stage 2 — Phase 6 Windows operations and reliability

**Status: PASS / COMPLETE**

Authoritative record: [`../../acceptance/phase-6-validation.md`](../../acceptance/phase-6-validation.md).

Mandatory Profile A covers packaged Windows runtime, native Windows worker, Git for Windows, Cloudflare Tunnel, and No-Auth network trust.

The completed matrix includes:

- 20/20 start/doctor/stop lifecycle;
- Bridge and managed-Tunnel abnormal exit recovery;
- native worker crash;
- Bridge/Tunnel port conflict behavior without killing foreign processes;
- stale process metadata recovery;
- actionable Git-missing diagnostics;
- secret-safe Cloudflare credential failures;
- disconnect during mutation without opaque replay;
- command timeout and owned process-tree cleanup;
- secret/log canary;
- spaces, Unicode/CJK paths, CRLF, LF, and supported long Windows paths;
- upgrade/rollback review of the pinned baseline.

WSL2 and OpenAI Secure MCP Tunnel are compatibility paths, not mandatory installed-product prerequisites.

### Stage 3 — Phase 7 final release acceptance

**Status: SOURCE FREEZE COMPLETE / POST-FREEZE SIGN-OFF EXTERNAL**

Authoritative criteria: [`../../acceptance/acceptance-test-plan.md`](../../acceptance/acceptance-test-plan.md).

The final gate covers:

- full registered tests;
- Ruff lint and format check;
- package/build checks;
- `git diff --check` equivalent evidence;
- clean-tree identity;
- exact 22-tool MCP contract;
- all normal functional paths;
- security negatives for path escape, sensitive files, binary/oversized input, command injection, dirty worktree, forged request hash, idempotency conflict, approval misuse, cross-session/cross-project isolation, checkpoint tamper, Git race, prompt injection, loopback/network trust, and secret/model-egress boundaries;
- reliability cases for replay, restart, approval persistence, disconnect, worker crash, timeout, external Git race, reconciliation, and 20-cycle lifecycle;
- at least 10 complete real-project remote tasks with operation/audit/Git lineage;
- explicit proof that ChatGPT is the only reasoning engine.

### Stage 4 — README, onboarding, and documentation consistency

**Status: DOCUMENTATION RELEASE BASELINE COMPLETE**

Current public documentation must state:

- packaged Windows runtime;
- native Windows worker;
- Git for Windows prerequisite;
- Cloudflare Profile A;
- optional OAuth Profile B;
- project add/remove hot reload;
- no arbitrary shell;
- no automatic push/merge/deploy;
- `NotSigned` and SmartScreen/reputation limitations;
- hosted-CI waiver;
- network-only identity limitation.

Historical release/report files remain historical evidence and must not override current architecture, guides, or acceptance criteria.

Final artifact onboarding still verifies that a clean Windows user can follow the README to install, initialize, register a project, run `doctor`, start, perform a safe read and controlled mutation, stop, and understand the identity limitations.

### Stage 5 — GitHub governance and CI

**Status: PASS / COMPLETE WITH HOSTED-CI WAIVER**

Repository governance includes:

- `CONTRIBUTING.md`;
- `CODE_OF_CONDUCT.md`;
- `CHANGELOG.md`;
- CI workflows;
- bug and feature issue forms;
- PR template;
- Dependabot configuration;
- protected public `main`;
- minimum workflow permissions and no unnecessary persisted checkout credentials.

GitHub-hosted execution was blocked before runner/job start by account billing/spending-limit state. This is an explicit **WAIVED / ACCEPTED RISK**, not a CI PASS and not a code/security test failure.

### Stage 6 — Secrets, privacy, and supply chain

**Status: PASS WITH DOCUMENTED LICENSE DISCREPANCY / HOSTED-CI WAIVER**

Completed controls include:

- current tracked-tree privacy regression;
- dependency audit;
- tracked-tree secret scan;
- all-ref Git-history secret scan;
- standard RC artifact scan;
- pinned Gitleaks assets;
- dependency-license inventory;
- pinned Windows PyInstaller build-tool closure;
- PyInstaller bootloader exception evidence;
- generated `THIRD_PARTY_NOTICES.txt`, provenance, and component license files;
- one-command Windows release build/audit pipeline;
- repeated clean-machine feedback loops that found and fixed real mutation/recovery issues without weakening Git/approval/CAS policy.

The final accepted runtime hotfix contract included clean-machine prepare/start/read/create/delete/cleanup with a clean `develop` disposable repository and explicit recovery of persisted unknown operations.

If a credential is ever found in history:

1. revoke/rotate it first;
2. clean Git history;
3. rescan;
4. rebuild the release candidate;
5. do not treat deletion from the current tree as sufficient history cleanup.

### Stage 7 — Release packaging and clean machine

**Status: SOURCE FREEZE COMPLETE / FINAL ARTIFACT SIGN-OFF EXTERNAL**

The repository already provides:

- packaged executable;
- Inno Setup installer;
- Windows release ZIP;
- SHA-256 manifest flow;
- clean-Windows validation harness;
- PowerShell 5.1-compatible validation path;
- bundled `cloudflared`;
- optional bundled `tunnel-client`;
- DPAPI transport-secret handling;
- native Windows worker;
- installer identity and upgrade-ownership checks.

The already audited RC completed production clean-machine `Prepare -> Start -> remote contract -> Cleanup` against disposable `phase5-clean`, ending with a clean Git worktree.

The stable artifact still requires a rebuild from the **final public commit** because that commit changes source/artifact identity.

Required post-freeze artifact work:

- rebuild installer and ZIP from the exact final public commit;
- regenerate and verify SHA-256;
- rerun artifact/security scan and exact source/artifact identity checks;
- verify `SOURCE_COMMIT.txt` and `BUILD_PROVENANCE.json`;
- perform final package/README onboarding/Start/Cleanup/uninstall sign-off;
- bind release metadata and final checksums without modifying the tag target;
- complete final release sign-off;
- only then create tag `v0.1.0` and publish the GitHub Release.

## 6. Current blockers

### P0 — must close before stable publication

1. **Final release-only public commit** — synchronize/freeze the unique release source identity.
2. **Exact-commit artifact rebuild** — rebuild installer and ZIP; regenerate SHA-256 and artifact/source identity evidence.
3. **Final clean-machine sign-off** — package, README onboarding, disposable repo, cleanup, and uninstall.
4. **Final release metadata binding** — release notes/known limitations/checksums must match exact commit and artifacts.
5. **Final Release Gate and publication** — sign off, then tag and publish.

Do not repeat already closed P0 work unless later changes invalidate its evidence. Closed items include the `NotSigned` decision, GitHub governance, Phase 6 mandatory matrix, Phase 7 functional/security/reliability work, 10/10 real-project tasks, repository/history audits, dependency/license sign-off, accepted clean-machine contract, and documentation consistency.

### P1 — must remain explicitly disclosed

- GitHub hosted CI: **WAIVED / ACCEPTED RISK** because billing/spending-limit blocked runner startup;
- `codemcp==0.3.0` MIT metadata vs bundled Apache-2.0 license-file discrepancy;
- Profile A provides network trust, not human-user identity;
- `v0.1.0` is intentionally `NotSigned`; SmartScreen/reputation warnings remain a known limitation.

### P2 — later `v0.1.x` / `v0.2`

Potential later work:

- deeper CodeQL/dependency review;
- automated release workflow;
- code-signing automation;
- additional platforms;
- additional transport adapters;
- multi-user identity/RBAC;
- fuller live OAuth Profile B matrix;
- codemcp fork/replacement only if real compatibility needs justify it.

Native Windows mutation is **not** P2; it is already the default implementation.

## 7. Recommended remaining execution order

From the frozen source state, execute only genuine remaining work:

1. documentation/status synchronization — **COMPLETE**;
2. signing decision — **COMPLETE: NotSigned / accepted limitation**;
3. GitHub final governance — **COMPLETE WITH HOSTED-CI WAIVER**;
4. create/freeze the final release-only public commit;
5. rebuild installer and ZIP from that exact commit;
6. regenerate SHA-256, artifact/security scan, and exact source/artifact identity evidence;
7. run final clean-machine package/README/disposable-repo/cleanup/uninstall sign-off;
8. bind release notes, known limitations, artifacts, and checksums to the final identity;
9. complete Final Release Gate sign-off;
10. tag `v0.1.0` and publish the GitHub Release.

Principles:

> All final evidence must bind to one release-candidate source identity.  
> Any change that affects release artifacts or security semantics invalidates the affected gates and requires rerun.

## 8. Final Release Gate

Stable `v0.1.0` may be approved only when every row below is satisfied:

| Gate | Requirement |
| --- | --- |
| Release identity | Exact branch/commit/lock/artifact identity recorded |
| Automated suite | Final-RC tests/lint/format/build PASS |
| MCP contract | Exact 22-tool surface and security contract VERIFIED |
| Functional | Core MCP normal paths PASS |
| Security | Negative matrix / threat-model P0 paths PASS |
| Reliability | Crash/restart/timeout/disconnect/unknown/reconcile/rollback PASS |
| Phase 6 | Mandatory Windows real-host operations PASS |
| Real projects | 10/10 remote tasks with complete operation/audit/Git lineage |
| ChatGPT-only boundary | No hidden model/provider/agent loop |
| Network trust | Profile A live boundary remains PASS |
| Secrets | Working tree + Git history + artifact scans PASS |
| Supply chain | Dependency vulnerability/license review PASS |
| Docs | README/architecture/guides/limitations match implementation |
| GitHub CI | **WAIVED / ACCEPTED RISK**; runner/jobs blocked by billing, not reported as PASS |
| Packaging | Final clean-machine installer/ZIP PASS |
| Cleanup | Uninstall/cleanup contract PASS |
| Integrity | SHA-256 for final artifacts VERIFIED |
| Signing | Signed, or `NotSigned` explicitly accepted and documented |
| Git | Clean worktree; tag points to the unique accepted commit |

Any unresolved P0 blocker, unexplained skip, secret exposure, unsafe mutation ambiguity, or documentation/behavior conflict keeps:

```text
release_decision = BLOCKED
```

## 9. Definition of done

Open-source readiness is complete only when:

1. the default architecture is clearly the packaged Windows runtime + native Windows worker;
2. the recommended personal public path is clearly Cloudflare Profile A network trust;
3. optional Secure MCP/OAuth/WSL2 compatibility paths do not contaminate default-product claims;
4. legal, security, contribution, and governance files are complete;
5. Phase 6 is PASS;
6. Phase 7 is PASS;
7. 10 real remote tasks are PASS;
8. working-tree/history/artifact secret scans are PASS;
9. dependency vulnerability/license review is PASS;
10. README onboarding is independently executable on clean Windows;
11. the hosted-CI waiver and repository governance state are explicit;
12. final installer/ZIP can be reproduced from one accepted commit;
13. final artifact clean-machine acceptance is PASS;
14. published SHA-256 values match final artifacts;
15. known limitations include network-only identity and signing state;
16. the release tag points exactly to the accepted commit;
17. the GitHub Release lets a third party install, diagnose, and begin controlled use without hidden developer-machine state.

Only then does `codemcp-remote` move from **pre-release / controlled private operation** to **stable public open-source release**.
