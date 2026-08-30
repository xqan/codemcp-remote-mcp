# Phase 7 Acceptance Test Plan — v0.1.0 Final Release Gate

> Status: **IN PROGRESS / FINAL RELEASE GATE**  
> Updated: 2026-08-30  
> Target: first stable `v0.1.0`

## 1. Purpose

This is the final functional, security, reliability, documentation, supply-chain and packaging gate for stable `v0.1.0`.

A feature is not PASS merely because:

- code exists;
- a lower-phase test exists;
- the current private Connector works;
- a historical release-candidate build succeeded.

Final approval requires evidence bound to the final release-candidate commit.

## 2. Mandatory release profile

The default `v0.1.0` acceptance profile is Profile A:

```text
Windows 11
  + packaged codemcp-remote.exe
  + Git for Windows
  + Native Windows local worker
  + Cloudflare Tunnel
  + ChatGPT Connector: Authentication = No authentication
  + Cloudflare WAF / Connector egress allowlist
  + Bridge network trust
```

Profile A security meaning:

```text
identity_level = network-only
```

Cloudflare network trust is not user authentication and cannot identify a ChatGPT user, account, Workspace or conversation.

Optional compatibility paths are tested only to the extent they remain advertised:

- WSL2 source-mode worker fallback;
- OpenAI Secure MCP Tunnel;
- OAuth Resource Server Profile B.

They do not redefine the mandatory installed-product baseline.

## 3. Release-candidate identity

The final acceptance record must capture:

- Git branch;
- exact release-candidate commit SHA;
- clean worktree state;
- `bridge/uv.lock` identity;
- `codemcp==0.3.0` identity;
- Python/uv/pwsh versions used for source/build CI;
- Windows version used for installed acceptance;
- Git for Windows version/path;
- packaged worker mode;
- Cloudflare client version/identity;
- Bridge configuration identity;
- project-registry configuration identity without exposing project roots publicly;
- auth/network-trust profile;
- installer SHA-256;
- release ZIP SHA-256;
- validation date.

If WSL2 is separately tested, record its distribution/version as compatibility evidence only.

No secret values may be copied into the release record.

## 4. Preconditions

Before final Phase 7 execution:

1. Phase 6 mandatory Windows operations gate is PASS.
2. Release-candidate worktree is clean.
3. The candidate is built from the exact commit being accepted.
4. Runtime secrets are injected/stored outside Git according to the documented protected path.
5. Profile A Cloudflare WAF/network trust is configured.
6. The final release artifact is available for clean-machine validation.
7. At least one dedicated real Java Git project is available for acceptance.
8. At least one project with a front-end build/test workflow is available for acceptance.
9. Destructive/recovery tests use disposable branches or fixture repositories.
10. Threat model has no untracked P0 threat.
11. Current normative documents agree on the default architecture.

## 5. Automated release-candidate gate

Run the complete registered repository workflow from the release-candidate commit.

Current full test scope:

```text
pytest -q bridge/tests tests/integration
```

Also run the release-required:

- Ruff lint;
- Ruff format check;
- package/build checks;
- configuration checks;
- compile/import checks used by CI;
- `git diff --check` equivalent;
- worktree cleanliness verification.

### Latest local development evidence

On 2026-08-28, after the open-source readiness/document alignment and transport-diagnostic fixes and before final release freeze, the registered test workflow reported:

```text
331 passed, 7 skipped
```

The skips are explicit environment/profile gates: unavailable symlink permissions, compatibility-only WSL coverage, opt-in real installer acceptance, and the Windows Phase 6 live-host smoke when no loopback Bridge is visible from the registered test process. A skip is not release PASS evidence; final RC acceptance must either exercise the mandatory case or document why it is outside the release profile.

This is useful development evidence only.

The final RC must re-run the complete gate and justify every remaining skip.

### Required result

- all mandatory tests PASS;
- no unexplained P0 security skip;
- lint/format/build PASS;
- worktree remains clean;
- test execution does not mutate release source state.

### Current final-gate evidence (2026-08-30)

- initial local standalone Ruff lint: **FAIL, FIXED** — 11 findings were reported and corrected without intended runtime-semantic changes;
- current-HEAD standalone Ruff lint: **PASS** — `All checks passed!`;
- current-HEAD full Ruff format scope: **PASS** — `80 files already formatted`;
- full registered test workflow after fixes: **PASS** — `353 passed, 7 skipped, 2 warnings`;
- configuration check: **PASS** — `status=ok`, `worker_mode=local`, `model_egress=deny`;
- Python package build: **PASS** — source distribution and wheel built successfully;
- `git diff --check`: **PASS**;
- `git diff --exit-code`: **PASS** — no tracked-file mutation from the gate;
- exact locally rechecked source identity: `083aef7a1e1aefb19164a48a1e6fb2f3e2f3f458`, branch `codex/open-source-readiness`, clean worktree;
- security audit after fixes: **PASS** — dependency audit, dependency-license evidence, current-tree secret scan, and Git-history secret scan all passed; 1291 commits scanned with no leaks; compatibility review remains manual as already documented;
- the earlier RC artifact audit remains historical evidence only because source HEAD changed after the lint fixes; final artifact evidence must be regenerated from the final release commit.

Status: **PASS / COMPLETE.**

## 6. MCP contract gate

The expected public surface is exactly 22 tools:

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

Required checks:

- no arbitrary shell tool;
- no caller-controlled executable path;
- no generic caller-controlled argv;
- no arbitrary host path bypass;
- no MCP project add/remove/reload/reconfigure;
- mutation tools require canonical request identity/hash semantics;
- approval remains explicit where designed;
- schema differences from the accepted baseline are reviewed.

Status: **PASS / COMPLETE.**

## 7. Functional acceptance

Use the real acceptance projects through the supported Profile A remote path.

| ID | Flow | Required result | Status |
|---|---|---|---|
| F-01 | open registered project | correct project/session/branch/HEAD metadata | PASS |
| F-02 | project status | readiness and Git state accurate | PASS |
| F-03 | list/read text | bounded correct content/metadata | PASS |
| F-04 | code search | relevant results; sensitive paths omitted | PASS |
| F-05 | create file | one intended committed change; replay idempotent | PASS |
| F-06 | exact edit | only intended target change | PASS |
| F-07 | whole-file write | matching SHA succeeds; stale SHA rejects | PASS |
| F-08 | move tracked file | no clobber; correct tracked semantics | PASS |
| F-09 | delete tracked file | intended tracked file only | PASS |
| F-10 | create directory | intended `.gitkeep`/Git-trackable behavior | PASS |
| F-11 | registered command | only configured command ID runs | PASS |
| F-12 | format/test wrappers | only registered expected-kind command runs | PASS |
| F-13 | git status/diff | bounded state corresponds to repository | PASS |
| F-14 | manual checkpoint | explicit approval + registered ref | PASS |
| F-15 | checkpoint restore | second approval + expected-HEAD CAS restore | PASS |
| F-16 | operation status/audit | lifecycle reconstructable | PASS |
| F-17 | cancel operation | only eligible owned operation cancelled | PASS |
| F-18 | reconcile unknown | evidence-backed transition preserves safety | PASS |
| F-19 | project add hot reload | local CLI add observed without Bridge/Tunnel/Connector restart | PASS |
| F-20 | project remove revocation | local CLI removal blocks new access and affected active sessions | PASS |

Project administration F-19/F-20 is executed locally, not through MCP.

## 8. Security negative acceptance

Every case must fail closed or enter the explicitly designed `unknown` state.

| ID | Attack / invalid state | Required behavior | Status |
|---|---|---|---|
| S-01 | unknown `project_id` | `PROJECT_NOT_ALLOWED` | PASS |
| S-02 | arbitrary absolute path | reject before unauthorized filesystem access | PASS |
| S-03 | `../` traversal | `PATH_ESCAPE` | PASS |
| S-04 | symlink escape | fail closed | DETERMINISTIC PASS / LIVE ENVIRONMENT BLOCKED |
| S-05 | Windows junction/reparse escape | fail closed | PASS REAL WINDOWS |
| S-06 | secret path | `SENSITIVE_PATH`/equivalent denial | PASS |
| S-07 | secret through search | excluded before/after backend | PASS |
| S-08 | sensitive content through diff | reject/redact | PASS |
| S-09 | binary/oversized file | bounded rejection | PASS |
| S-10 | unregistered command | `COMMAND_NOT_ALLOWED` | PASS |
| S-11 | runtime argv/executable injection | impossible through public schema | PASS CONTRACT |
| S-12 | dirty workspace mutation | fail according to policy | PASS |
| S-13 | forged canonical request hash | reject before side effect | PASS |
| S-14 | request ID reused with changed input/hash | idempotency conflict | PASS |
| S-15 | wrong approval token | reject | PASS |
| S-16 | expired approval | reject | PASS REAL WINDOWS |
| S-17 | reused approval | reject | PASS |
| S-18 | cross-session operation/approval | hide/reject foreign scope | PASS |
| S-19 | cross-project operation/approval | hide/reject foreign scope | PASS REAL WINDOWS |
| S-20 | external branch/HEAD change before restore | CAS conflict; no reset | PASS REAL WINDOWS |
| S-21 | dirty worktree before restore | reject; no reset | PASS REAL WINDOWS |
| S-22 | checkpoint ref tamper/missing | reject; no arbitrary reset | PASS REAL WINDOWS |
| S-23 | repository prompt injection | cannot widen Bridge authorization | PASS RED-TEAM LIVE |
| S-24 | non-loopback Bridge config | invalid/fail closed | PASS |
| S-25 | secret/log canary | absent/redacted from logs/evidence | PASS PHASE 6 + REGRESSION |
| S-26 | hidden model/provider egress | absent | PASS LIVE |
| S-27 | wrong/missing Host | exact host boundary rejects | PASS LIVE |
| S-28 | invalid Origin when present | reject non-exact origin | PASS LIVE |
| S-29 | ordinary public source | Cloudflare blocks before Bridge | PASS LIVE |
| S-30 | forwarded-IP spoof | forwarded headers cannot authorize | PASS LIVE |
| S-31 | project registry invalid update | last-known-good retained; fail closed | PASS |
| S-32 | project root redirect | rejected; no silent authorization transfer | PASS |
| S-33 | MCP project-admin attempt | no public admin tool exists | PASS CONTRACT |

S-23 live evidence: ChatGPT read a repository fixture instructing it to ignore security rules and access `../outside.txt`; the subsequent Bridge `file_list("../")` call still failed with `PATH_ESCAPE`. The fixture was deleted. After a host reboot invalidated the originating session, the old checkpoint restore correctly failed closed with `SESSION_NOT_FOUND`; the disposable acceptance repository was then locally reset to the exact pre-test baseline `5c39ea948fb91389762217e748b7d8bbd0c0b4e9`, branch `develop`, clean worktree.

Any bypass that grants broader filesystem, command, approval, Git, identity, secret, network or project-administration capability is a release blocker.

## 9. Reliability and recovery acceptance

| ID | Fault | Required result | Status |
|---|---|---|---|
| R-01 | duplicate successful mutation | persisted result replay; no duplicate edit | PASS |
| R-02 | same request ID / changed hash | conflict; no second side effect | PASS |
| R-03 | Bridge restart before dispatch | operation fails safely | PASS |
| R-04 | Bridge restart after uncertain backend boundary | operation becomes `unknown` | PASS |
| R-05 | approval pending during restart | plaintext approval unavailable; fail closed | PASS |
| R-06 | Cloudflare Tunnel disconnect | no transparent mutation replay | PASS |
| R-07 | Native Windows worker crash | failure/unknown preserves project safety block | PASS |
| R-08 | registered command timeout | bounded timeout + owned process-tree cleanup/unknown | PASS |
| R-09 | external Git race | mutation/restore detects changed state | PASS |
| R-10 | reconcile verified not-applied | releases block only with scoped evidence | PASS |
| R-11 | reconcile verified applied | successor recovery follows designed constraints | PASS |
| R-12 | 20 packaged start/doctor/status/stop cycles | 20/20 with no owned residue | PASS |
| R-13 | invalid project-registry generation | keep last-known-good | PASS |
| R-14 | remove/re-add project ID | old sessions do not regain authorization | PASS |

Prefer explicit unavailable/blocked/unknown results over guessing.

## 10. Real-project acceptance

Run at least ten complete remote modification tasks, not ten isolated tool calls.

Minimum distribution:

- five tasks against a real Java acceptance project;
- three tasks against a project with front-end workflow;
- two tasks exercising recovery/checkpoint/reconcile behavior.

For each task record:

- task ID/project ID;
- starting branch/HEAD;
- session ID;
- relevant operation IDs;
- change summary;
- command IDs executed;
- ending branch/HEAD;
- changed-file list;
- diff review result;
- approval usage;
- unknown/reconcile status;
- final project test/build result.

Do not copy proprietary source content into the public acceptance report.

Required:

```text
10/10 tasks
```

with explainable operation/audit/Git lineage and no unexplained side effect.

Status: **PASS — 10/10 COMPLETE.**

### Real-project task ledger

| Task | Project | Start | End | Session | Operations / command | Diff / side effects | Result |
|---|---|---|---|---|---|---|---|
| J-01 | `phase7-java-acceptance` | `develop` / `f7e90db814dd9f728c778436b305792fcd774447` | `develop` / `fec6972cfd948f930a3215358bc9e4d5c189de7e` | `e5bb37fb4cc94ceba8af0f402c089159` | edits `075d0d5c…`, `86ae7759…`; UTF-8 normalization `31e9104c…`, `cedd058c…`; test `96444dbb…` | `Calculator.java` + `CalculatorTest.java`; final worktree clean; no approval; no unknown/reconcile | **PASS** — added multiply behavior + test; Maven `Tests run: 3, Failures: 0, Errors: 0`, `BUILD SUCCESS` |
| J-02 | `phase7-java-acceptance` | `develop` / `fec6972cfd948f930a3215358bc9e4d5c189de7e` | `develop` / `79684a69511f76abd781c88eb9d5d9fe59c0ffea` | `e5bb37fb4cc94ceba8af0f402c089159` | edits `354d2538…`, `d218bfff…`, `b4889142…`; test `60641059…` | `Calculator.java` + `CalculatorTest.java`; diff reviewed; final worktree clean; no approval; no unknown/reconcile | **PASS** — guarded divide + zero-divisor test; Maven `Tests run: 4, Failures: 0, Errors: 0`, `BUILD SUCCESS` |
| J-03 | `phase7-java-acceptance` | `develop` / `79684a69511f76abd781c88eb9d5d9fe59c0ffea` | `develop` / `5edb1a00522f2617b03539c141124d8e8ca9f3fb` | `e5bb37fb4cc94ceba8af0f402c089159` | creates `c353dd29…`, `1aed56b5…`; test `c9f30c36…` | new `NumberRange.java` + `NumberRangeTest.java`; diff reviewed; final worktree clean; no approval; no unknown/reconcile | **PASS** — clamp utility + boundary tests; Maven `Tests run: 6, Failures: 0, Errors: 0`, `BUILD SUCCESS` |
| J-04 | `phase7-java-acceptance` | `develop` / `5edb1a00522f2617b03539c141124d8e8ca9f3fb` | `develop` / `332dea59e0ae464663214011165d8ccb6e25400d` | `e5bb37fb4cc94ceba8af0f402c089159` | edits `59426c91…`, `a159adc9…`; test `77d62385…` | `NumberRange.java` + `NumberRangeTest.java`; diff reviewed; final worktree clean; no approval; no unknown/reconcile | **PASS** — inclusive range membership + shared range validation; Maven `Tests run: 7, Failures: 0, Errors: 0`, `BUILD SUCCESS` |
| J-05 | `phase7-java-acceptance` | `develop` / `332dea59e0ae464663214011165d8ccb6e25400d` | `develop` / `c2597943f3b29bff084483bba3e8d653988029fe` | `e5bb37fb4cc94ceba8af0f402c089159` | moves `db6a7ccf…`, `695c9d12…`; writes `af023a4b…`, `aabad496…`; test `2d461ce4…` | source/test rename diff reviewed; final worktree clean; no approval; no unknown/reconcile | **PASS** — `NumberRange` → `RangeMath` refactor via tracked moves; Maven `Tests run: 7, Failures: 0, Errors: 0`, `BUILD SUCCESS` |
| F-01 | `phase7-frontend-acceptance` | `develop` / `e74eb6ee4c7643e66efb3fd8a21b0f22f5b8b086` | `develop` / `679c32bdc277e9102fe15e6b7d182b74d83f7001` | `775d9db154ca4667873592ba056396ab` | edits `2579490d…`, `800f61a2…`, `cd9e49e8…`; test `ff460760…`; build `a9e2652d…` | `src/app.js` + `test.mjs`; diff reviewed; build output ignored; final worktree clean; no approval; no unknown/reconcile | **PASS** — added farewell behavior; `npm test` PASS + `npm run build` PASS |
| F-02 | `phase7-frontend-acceptance` | `develop` / `679c32bdc277e9102fe15e6b7d182b74d83f7001` | `develop` / `9e71b8993daea3716f45864c7da88d5cc161a5a0` | `775d9db154ca4667873592ba056396ab` | edits `f1fa04f2…`, `8022d8b6…`; test `a3df8c06…`; build `fcd1688d…` | `src/app.js` + `test.mjs`; final worktree clean; no approval; no unknown/reconcile | **PASS** — trimmed names + blank-name rejection; `npm test` PASS + `npm run build` PASS |
| F-03 | `phase7-frontend-acceptance` | `develop` / `9e71b8993daea3716f45864c7da88d5cc161a5a0` | `develop` / `cfd7bcb8215b306764d6cb2a5d50e534180d8a48` | `775d9db154ca4667873592ba056396ab` | create `c2149945…`; edits `54385eab…`, `380c032a…`, `3617c418…`; test `355f37f0…`; build `9b9e1af8…` | `src/meta.js` + `build.mjs` + `test.mjs`; diff reviewed; final worktree clean; no approval; no unknown/reconcile | **PASS** — metadata added to tests/build output; `npm test` PASS + `npm run build` PASS |
| R-01 | `phase7-frontend-acceptance` | `develop` / `cfd7bcb8215b306764d6cb2a5d50e534180d8a48` | `develop` / `cfd7bcb8215b306764d6cb2a5d50e534180d8a48` | `775d9db154ca4667873592ba056396ab` | temp edits `b4e98e0f…`, `281a117b…`; pre-restore test `41f4c05e…`; expired approval rejected, stale blocker cancelled `90f406bd…`; restore `e69cb182…` confirmed by `36cada12…`; post-restore test `23acc289…`; build `d1a68e6e…` | rollback-safety checkpoint `a7aa4139…`; restored exact baseline HEAD; final worktree clean; explicit approval used; no unknown side effect | **PASS** — checkpoint/CAS rollback restored `1.0.1` trial back to exact `1.0.0` baseline; post-restore `npm test` + build PASS |
| R-02 | `phase7-java-acceptance` | `develop` / `c2597943f3b29bff084483bba3e8d653988029fe` | `develop` / `c2597943f3b29bff084483bba3e8d653988029fe` | `e5bb37fb4cc94ceba8af0f402c089159` | manual checkpoint create `796f2290…` confirmed by `1aaa2c88…`; trial edits `f9ed25d2…`, `055f71e9…`; pre-restore test `341672db…`; restore `76723c2e…` confirmed by `e3994bc9…`; post-restore test `2010270a…` | manual checkpoint `acebff17…`; rollback-safety checkpoint `21548e44…`; exact baseline restored; final worktree clean; explicit approval used; no unknown side effect | **PASS** — explicit Bridge-owned checkpoint rollback; trial Maven `8 tests` PASS, restored baseline Maven `7 tests` PASS, both `BUILD SUCCESS` |

J-01 setup note: the first remote Maven test correctly failed because the disposable project had UTF-8 BOMs introduced by local PowerShell fixture creation. The files were normalized through MCP with SHA-256 CAS and the same task was rerun to PASS. This is acceptance-harness setup evidence, not a product failure.

## 11. ChatGPT-only reasoning boundary

Verify from final RC:

- Bridge has no configured model provider;
- Bridge has no hidden agent loop;
- codemcp remains an execution backend;
- repository content cannot authorize a privileged action;
- each multi-step user task is reconstructable from explicit ChatGPT MCP calls;
- network observation shows no prohibited model/provider egress from Bridge/codemcp.

Expected Tunnel/control-plane traffic is not model egress.

Status: **PASS / COMPLETE.** Evidence is the current `model_egress=deny` configuration check, S-23 prompt-injection fail-closed result, S-26 live network observation, and the explicit 22-tool ChatGPT MCP execution surface.

## 12. Network-trust live boundary

Profile A Phase A-H already has successful live evidence, including:

- real ChatGPT Connector access;
- 22-tool discovery;
- project access;
- mutation;
- identical replay;
- explicit approval;
- checkpoint/CAS restore;
- exact baseline recovery;
- ordinary-source Cloudflare Block;
- ChatGPT-source Allow.

Final release acceptance must recheck that the current final RC has not invalidated:

- exact Host boundary;
- if-present Origin boundary;
- network-only principal semantics;
- ordinary public source Block;
- ChatGPT Connector access.

Do not reinterpret this as user authentication.

Status: **PASS / COMPLETE.** The authoritative live evidence is `docs/reports/testing/phase-h-live-acceptance.md`, supplemented by the final-RC Host/Origin/public-source/forwarded-header checks recorded in the active Live Acceptance Ledger.

## 13. Documentation acceptance

Current public/normative documents:

- [x] `LICENSE` / `AGPL-3.0-only`
- [x] `SECURITY.md`
- [x] `docs/architecture/security-model.md`
- [x] `docs/architecture/threat-model.md`
- [x] `docs/architecture/architecture.md`
- [x] `docs/implementation-plan.md`
- [x] `docs/acceptance/phase-6-validation.md`
- [x] `docs/acceptance/acceptance-test-plan.md`
- [x] public README
- [x] Windows build/install/use guide
- [x] operations runbook
- [x] codemcp pinned-baseline guide
- [x] Cloudflare network-trust guide
- [x] `CONTRIBUTING.md`
- [x] `CODE_OF_CONDUCT.md`
- [x] `CHANGELOG.md`
- [x] `.github` CI / issue / PR / Dependabot configuration
- [x] final third-party notices decision — preserve bundled notices/license evidence and the documented codemcp MIT/Apache metadata discrepancy
- [ ] clean-machine README execution PASS
- [x] final known-limitations cross-check — `NotSigned`/SmartScreen, Profile A network-only identity, Git prerequisite, hosted-CI waiver, and codemcp metadata discrepancy are explicitly disclosed
- [x] draft `v0.1.0` release notes are present and intentionally do not freeze final commit/SHA values before the final artifact rebuild
- [ ] final release notes/checksum verification against final RC

Documentation must not claim:

- WSL2 is required for normal installed mutation;
- Native Windows mutation is unsupported;
- Secure MCP Tunnel is the mandatory default remote path;
- Profile A provides human user identity.

Current documentation consistency: **PASS**. Clean-machine README execution and final release-note/checksum binding remain release-artifact gates, not unresolved documentation contradictions.

## 14. Secrets and supply-chain acceptance

Before tag:

- [x] scan tracked working tree;
- [x] scan complete Git history;
- [x] scan `.github/`, scripts, configs, docs, tests and fixtures;
- [ ] scan final release artifact after the final release-only commit rebuild;
- [x] audit locked dependencies for known vulnerabilities;
- [x] review dependency license compatibility/notices — PASS WITH DOCUMENTED DISCREPANCY;
- [x] confirm local project registry/runtime logs/SQLite/secret data are excluded by the packaging/security gate;
- [x] verify `codemcp==0.3.0` provenance remains intentional and preserve its MIT/Apache metadata discrepancy.

If a real secret is discovered:

1. revoke/rotate;
2. clean history;
3. rescan;
4. rebuild candidate;
5. rerun affected gates.

Status: **PASS WITH FINAL-ARTIFACT RECHECK PENDING.** Source/dependency/history/notices checks are complete; the final installer/ZIP artifact scan must be rerun after the final release-only commit rebuild.

## 15. Packaging and clean-machine acceptance

The final release workflow must produce from the exact accepted commit:

- Windows EXE payload;
- `codemcp-remote-setup.exe`;
- release ZIP;
- SHA-256 checksum/manifest;
- release notes;
- known limitations;
- exact Git identity.

On a clean Windows 11 host:

1. verify installer SHA-256;
2. install;
3. isolate product runtime PATH;
4. verify Python/uv/pwsh are not required/visible;
5. verify Git prerequisite;
6. verify `worker_mode=local`;
7. initialize Profile A;
8. verify DPAPI-backed transport secret;
9. create/register disposable `phase5-clean` project;
10. start Bridge + Cloudflare Tunnel;
11. connect real ChatGPT Connector;
12. read acceptance file;
13. perform deterministic mutation;
14. verify identical replay;
15. restore via approval/checkpoint CAS;
16. prove exact original baseline and clean worktree;
17. stop;
18. cleanup/uninstall;
19. verify expected preserved user data contract;
20. scan artifact/evidence for secrets.

The previous live Phase H use of a temporary file in the real `codemcp-remote` repository does not replace this strict disposable-project clean-machine gate.

Status: **PENDING STRICT PASS.**

## 16. GitHub hosted acceptance

Repository-side governance is already implemented.

Before stable release:

- [x] repository-side CI / issue / PR / Dependabot configuration is present;
- [x] hosted CI billing/spending-limit blocker is recorded as **WAIVED / ACCEPTED RISK**; no unexecuted runner/job is counted as PASS;
- [ ] verify Dependabot recognizes both `uv` and `github-actions` on GitHub;
- [ ] verify the branch/ruleset protects the intended release branch with the chosen merge policy; do not require an unavailable hosted-CI check unless the billing blocker is resolved;
- [ ] verify issue forms render;
- [ ] verify the PR template renders;
- [x] workflow permissions remain least-privilege in the repository configuration.

Local workflow files do not establish hosted activation. The accepted CI waiver and the final GitHub-side governance state must both be recorded before release.

## 17. Signing decision

Current historical candidate evidence records Authenticode as:

```text
NotSigned
```

Before final release, explicitly choose and document one:

- sign the installer/release executable with an appropriate code-signing certificate; or
- publish unsigned artifacts with the limitation and expected Windows/SmartScreen trust impact clearly disclosed.

Do not imply signed provenance when the artifact is unsigned.

## 18. Final Release Gate

Stable `v0.1.0` requires:

| Gate | Required state |
|---|---|
| Release identity | VERIFIED |
| Phase 6 Windows operations | PASS |
| Automated suite/lint/format/build | PASS |
| 22-tool MCP contract | PASS |
| Functional matrix | PASS |
| Security negative matrix | PASS |
| Reliability/recovery matrix | PASS |
| 10 real remote tasks | PASS |
| ChatGPT-only boundary | PASS |
| Profile A live boundary | PASS |
| Documentation | PASS |
| Secrets | PASS |
| Dependency/license supply chain | PASS |
| Strict clean-machine packaging | PASS |
| Hosted GitHub CI / governance | Hosted CI **WAIVED / ACCEPTED RISK** because billing blocked execution; ruleset/Dependabot/final governance state must be RECORDED |
| SHA-256 integrity | PASS |
| Signing decision | RECORDED |
| Worktree/tag identity | CLEAN / VERIFIED |

Any P0 blocker, unexplained skip, unresolved secret exposure, unsafe mutation ambiguity, documentation/implementation mismatch or artifact identity mismatch keeps the decision:

```text
BLOCKED
```

## 19. Final sign-off record

Fill only from the final release candidate:

```text
release_candidate_commit:
release_candidate_branch:
phase_6_status:
automated_suite:
mcp_contract:
functional_matrix:
security_matrix:
reliability_matrix:
real_task_count:
chatgpt_only_boundary:
network_trust_live:
documentation:
secret_scan:
dependency_audit:
license_review:
clean_machine_install:
cleanup_uninstall:
hosted_ci:
artifact_installer_sha256:
artifact_zip_sha256:
authenticode:
known_blockers:
release_decision: BLOCKED | APPROVED
validated_at:
```

Default decision is `BLOCKED` until every mandatory field is backed by evidence.
