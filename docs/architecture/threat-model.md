# Threat Model

## Scope

This document tracks threats that matter to the `v0.1.0` architecture:

Recommended path: `ChatGPT Connector (No authentication) → OpenAI Connector egress → Cloudflare WAF IP List → Cloudflare Tunnel → loopback Bridge → native local worker → registered Git project`. The optional compatibility path uses the Secure MCP Tunnel, and Profile B adds OAuth Resource Server identity.

The protected assets are local source code, secrets, Git state, registered command boundaries, approval state, operation/audit integrity, and runtime Tunnel credentials.

The local operator account, trusted Bridge installation, trusted local configuration, and trusted toolchain are root assumptions. Compromise of those roots is documented as residual risk rather than claimed to be sandboxed.

## Risk conventions

- **Blocked**: design intends to reject the action before side effects.
- **Detect/reconcile**: a side effect can be uncertain, so the Bridge must preserve an `unknown` or conflict state.
- **Trusted boundary**: the current version intentionally relies on a local/operator assumption.

## Threat matrix

| Threat | Preconditions | Existing mitigation | Residual risk | Validation |
|---|---|---|---|---|
| Arbitrary `project_id` access | Caller knows or guesses another local path/project | Tools resolve only configured IDs; unknown IDs return `PROJECT_NOT_ALLOWED` | Local operator can intentionally register unsafe roots | Negative project-ID tests |
| `../` or absolute path escape | Caller submits crafted path | Relative-path normalization and resolved-root containment check | Filesystem race/OS edge cases require regression coverage | traversal/absolute-path tests |
| Symlink/junction/reparse escape | A project contains a link to outside the root | Existing path components are rejected when symlink/reparse points are detected | TOCTOU if local attacker can replace components concurrently; compromised local account is out of scope | Windows junction + WSL symlink tests |
| Sensitive-file read | Secret is stored under a recognizable name/path | Sensitive name/glob/suffix deny list; search excludes and post-filters sensitive paths | Secrets with ordinary filenames/content are not automatically classified | `.env`, key/cert, token-name read/search tests |
| Arbitrary shell | Caller wants a command not configured by operator | No generic shell tool; commands referenced by fixed ID | Operator can configure an unsafe command | unregistered-command and argv-injection tests |
| Command argument injection | Caller attempts to append flags or shell syntax | Public command call accepts command ID, not runtime argv | Unsafe quoting or backend behavior in configured command remains supply/config risk | metacharacter/extra-argument contract tests |
| Repository prompt injection | Malicious source/doc tells ChatGPT to bypass policy | Repository content is data; Bridge authorization is independent of prose | ChatGPT may still make undesirable but policy-valid edits | red-team prompt-injection scenario + verify policy cannot be widened |
| Malicious project configuration | Attacker changes trusted local command/project configuration | Configuration is treated as operator-controlled trusted policy; schema/defaults fail closed | Compromised local account can intentionally authorize dangerous behavior | config validation; document trust boundary |
| Secret exfiltration through results | Caller requests source/diff/log containing secret | sensitive-path denial, bounded output, filtering/sanitization, log policy | Secret in non-sensitive filename or generated command output may still leak | secret corpus tests + log/release scan |
| Approval token theft/replay | Attacker obtains an approval token | Random token; stored only as hash; TTL; one-time consume; operation binding | Token usable before expiry if stolen from live response/process memory | expired/used/wrong-token/cross-operation tests |
| Cross-session/project approval | Caller tries token on another scope | Approval/database binding to operation; operation/session/project checks | Implementation regressions could break binding | cross-session + cross-project negative tests |
| Forged idempotency hash | Caller sends a request hash for different parameters | Bridge recomputes canonical SHA-256 input and rejects mismatch | Hash function compromise considered impractical; canonicalization changes require compatibility care | forged-hash regression tests |
| Duplicate mutation replay | Tunnel/client retries after timeout | client request ID + canonical request hash + persisted operation result | Crash exactly around external side effect may still produce uncertainty | duplicate/restart/disconnect tests |
| Operation database corruption | SQLite state becomes inconsistent or is modified | explicit state machine, persistence checks, reconciliation paths | A compromised local account can alter DB; storage corruption can require manual recovery | restart-state tests; corruption handling where implemented |
| Tunnel disconnect during mutation | Network drops after dispatch | Bridge does not infer retry from reconnect; operation state is persisted | Caller may not know outcome until status/reconciliation | disconnect injection during mutation |
| Worker crash during mutation | codemcp process exits mid-write | worker lifecycle handling; uncertain side effect becomes `unknown` rather than implicit retry | Backend may have partially changed filesystem/Git | crash injection before/during/after write |
| Command timeout/process-tree failure | command hangs or child survives | bounded timeout and process-tree cleanup design | OS/process edge cases may leave child processes; Phase 6 validation still required | timeout + orphan-process tests |
| External Git modification race | IDE/user changes branch/HEAD/worktree during operation | clean baseline; branch/HEAD checks; per-project Bridge lock; rollback CAS | Bridge lock cannot prevent external tools from mutating the repo | concurrent external HEAD/worktree tests |
| Destructive rollback | Caller tries to overwrite newer work | checkpoint scope/ref verification, expected HEAD, clean worktree, second approval, safety checkpoint | Local manual Git actions remain outside Bridge control | branch/HEAD mismatch + dirty-worktree rollback tests |
| Log/audit leakage | errors/results contain token or source secret | token hashes persisted; diagnostics must redact runtime key; bounded summaries | Unknown command output may contain sensitive material | log corpus scan and explicit credential canaries |
| Model/provider egress | Bridge/dependency attempts model call | project policy denies model calls; no provider is part of intended Bridge design | A malicious/compromised dependency can make arbitrary network calls unless host network policy blocks it | dependency/source review + runtime network observation |
| Transport credential theft | local state/log/history exposes Cloudflare `TUNNEL_TOKEN`, optional OAuth verification secret, or Secure MCP control-plane key | Profile A supports DPAPI-backed tunnel secret storage; optional compatibility credentials remain outside repo/profile/logs; diagnostics redact token forms | Compromised process/local user can still read accessible process memory or protected local state under the same OS account | secret scan + diagnostic output tests |
| Dependency/supply-chain compromise | PyPI/upstream package or local interpreter is compromised | pin `codemcp==0.3.0`; lock file; planned dependency audit/CI | Version pinning does not prove package integrity or prevent compromise | dependency audit, lock review, release build provenance checks |
| Compromised ChatGPT workspace | attacker can send otherwise valid calls through connected workspace | Bridge still enforces local project/path/command/approval/Git policy | Policy-valid operations may still be harmful; v0.1.0 has no independent user identity/RBAC | document limitation; test that Tunnel identity does not bypass Bridge |
| Cloudflare allowlist mistaken for user authentication | operator assumes an allowed Connector egress IP identifies a person or conversation | Cloudflare WAF is documented and enforced as a network trust boundary; Profile A reports `identity_level = network-only`; Profile B is required for subject/client/scope identity | Any caller inside an allowed egress range shares the same network trust profile | deployment runbook and live Security Events must distinguish network admission from identity |
| WAF/IP enforcement bypassed in the Bridge | implementation trusts a forwarded client-IP header or reverse DNS | IP ranges and WAF rule remain external at Cloudflare Edge; Bridge rejects forwarded-header authorization assumptions and uses exact Host/Origin only | Misconfigured Cloudflare account, Tunnel ingress, or a directly exposed Bridge is outside application guarantees | WAF rule review, normal-IP `403`, loopback bind inspection |
| Compromised local OS user | attacker can edit config/code/DB/processes | none by design; this is a trusted boundary | Full compromise of Bridge guarantees | document as explicit non-goal |

## High-priority release threats

The following are P0 release blockers if unmitigated:

1. project-root escape;
2. arbitrary command/argument injection;
3. secret-file access through read/search/diff;
4. approval bypass or cross-scope replay;
5. canonical request-hash bypass or duplicate mutation replay;
6. rollback that can overwrite external Git changes;
7. mutation failure reported deterministically when the side effect is actually uncertain;
8. runtime credential leakage;
9. Bridge exposure beyond loopback contrary to configuration;
10. Cloudflare WAF/network admission being misrepresented as user authentication;
11. any hidden model/provider dependency that violates the documented execution boundary.

## P0 validation mapping

The table below maps each release-blocking threat to current evidence. A mapping is not a PASS: any item marked for Phase 6/7 still blocks release until that validation is actually executed successfully.

| P0 threat | Current automated evidence | Remaining release validation |
|---|---|---|
| Project-root escape | `test_phase2_policy.py::test_registry_rejects_unregistered_escape_and_sensitive_paths`; `test_phase2_server.py::test_local_mcp_contract_and_policy_rejections` | Native Windows junction/reparse and available symlink escape matrix; WSL symlink coverage is compatibility-only when that worker mode is advertised |
| Arbitrary command / argument injection | `test_phase2_policy.py::test_policy_rejects_dirty_workspace_and_command_drift`; unregistered-command rejection in `test_local_mcp_contract_and_policy_rejections` | Explicit public MCP schema check proving no runtime argv/shell surface |
| Secret read/search/diff | sensitive path + sensitive diff checks in `test_phase2_policy.py`; `test_phase2_server.py::test_code_search_excludes_sensitive_paths_before_and_after_grep` | Phase 7 secret corpus including non-obvious filenames and command-output canaries |
| Approval bypass / cross-scope replay | `test_phase3_persistence.py::test_approval_is_one_time_and_token_is_not_persisted`; approval lifecycle in `test_phase3_idempotency_approval_and_operation_status`; foreign-session operation rejection in `test_phase3_reconcile_unknown_mutation_releases_project_lock` | Explicit cross-project approval negative case |
| Canonical request hash / duplicate replay | `test_phase3_persistence.py::test_request_hash_is_bound_to_canonical_input`; `test_idempotency_replays_without_repeating_and_detects_hash_conflict`; Bridge-level replay tests in `test_phase2_server.py` | Tunnel-level duplicate/reconnect replay case |
| Rollback overwriting external Git changes | Phase 4 Git regression suite exists in `bridge/tests/test_phase4_git.py` | Re-run CAS matrix in Phase 7 with external branch/HEAD/worktree races |
| Incorrect handling of uncertain side effects | restart/unknown tests in `test_phase3_persistence.py`; cancellation and reconciliation tests in `test_phase2_server.py` | Worker crash, Tunnel disconnect, and timeout injection through real backend |
| Runtime credential leakage | approval plaintext persistence test covers approval tokens | Phase 6 log canary scan for Tunnel/runtime secrets; release artifact secret scan |
| Non-loopback Bridge exposure | loopback server configuration is exercised by local MCP contract tests | Clean-machine port/bind inspection proving no non-loopback listener |
| Hidden model/provider dependency | architecture and dependency source review are required by the implementation plan | Phase 7 dependency/source scan plus runtime network observation |

## Residual-risk policy

A threat may remain for `v0.1.0` only when:

- it is outside an explicit trusted boundary; or
- mitigation is present and remaining risk is documented;
- validation exists or is explicitly tracked as a release gate;
- the README does not imply a stronger guarantee.

Availability failures are acceptable when the safe alternative is to guess about a mutation. Fail closed and reconciliation take priority over transparent retry.

## Validation ownership

`docs/acceptance/acceptance-test-plan.md` is the executable release matrix. Until every mandatory P0 path in that plan has final release-candidate evidence, this threat model remains a design record rather than proof of release security.

Every new high-privilege MCP tool must add:

1. a trust-boundary review;
2. a row here or an explicit determination that existing rows cover it;
3. negative authorization tests;
4. failure/unknown-side-effect tests where mutation is possible;
5. documentation of any new secret, executable, Git, or identity scope.
