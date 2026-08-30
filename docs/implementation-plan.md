# codemcp-remote v0.1.0 Current Implementation Plan

> Updated: 2026-08-28  
> Release target: `v0.1.0`  
> Status: **PRE-RELEASE / RELEASE GATES IN PROGRESS**  
> Open-source release plan: [`plans/v0.1.0/open-source-readiness-plan.md`](plans/v0.1.0/open-source-readiness-plan.md)

## 1. Goal

codemcp-remote is a policy-controlled local MCP bridge that lets **ChatGPT remain the only reasoning engine** while operating on explicitly registered local Git repositories.

The installed Windows product must provide a bounded execution surface instead of arbitrary remote shell access:

- ChatGPT decides what to read, search, edit, test, inspect, restore, or reconcile;
- the Bridge validates authorization, paths, commands, operation identity, approvals, audit and Git safety;
- codemcp is an execution backend, not a second reasoning agent;
- remote transports publish only the Bridge;
- the Bridge never grants access to an arbitrary local path or caller-supplied shell command.

Stable `v0.1.0` is not approved until the mandatory Phase 6/7, supply-chain, clean-machine packaging and hosted CI gates are complete.

## 2. Current Product Baseline

### 2.1 Installed Windows runtime

The `v0.1.0` installed product targets:

- Windows 11 x64-compatible;
- packaged `codemcp-remote.exe`;
- `codemcp-remote-setup.exe`;
- Git for Windows as an explicit runtime prerequisite;
- Native Windows local codemcp worker as the default worker;
- bundled `cloudflared`;
- optional bundled OpenAI `tunnel-client`;
- no Python, `uv`, PowerShell 7 or WSL2 requirement for normal installed runtime use.

Source development still requires Python 3.12+, `uv` and PowerShell 7.

WSL2 Ubuntu remains an explicit source-mode compatibility fallback only.

### 2.2 Recommended remote profile

The recommended personal deployment is Profile A:

```text
ChatGPT Connector
  Authentication = No authentication
        |
        v
OpenAI / ChatGPT Connector egress
        |
        v
Cloudflare Edge / WAF IP allowlist
        |
        v
Cloudflare Tunnel
        |
        v
127.0.0.1:46200/mcp
        |
        v
codemcp-remote Bridge
        |
        v
Native Windows codemcp worker
        |
        v
registered local Git repository
```

Profile A rules:

- `auth.mode = "none"`;
- `network_trust.mode = "cloudflare-chatgpt"`;
- canonical `allowed_hosts` must be non-empty;
- Cloudflare WAF enforces the OpenAI/ChatGPT Connector egress allowlist;
- the Bridge does not authorize from forwarded client-IP headers;
- `identity_level = network-only`;
- network trust is **not user authentication** and cannot identify a ChatGPT user, Workspace, account or conversation.

Profile B remains optional:

```text
auth.mode = "oauth-resource-server"
```

It is retained for deployments that require subject/client/scope identity and the existing external OAuth Resource Server contract.

OpenAI Secure MCP Tunnel remains an optional compatibility transport, not the default `v0.1.0` personal deployment path.

## 3. Architecture Contract

### 3.1 ChatGPT is the only reasoning engine

The Bridge and codemcp must not contain or invoke:

- model providers;
- LLM or embedding API clients;
- hidden agent loops;
- autonomous task planners;
- natural-language-to-shell dispatch;
- model routing.

Every reasoning step that changes the next action must originate from ChatGPT through another explicit MCP call.

### 3.2 Bridge is the security and lifecycle boundary

The Bridge owns:

- project registration lookup and authorization;
- session lifecycle;
- operation lifecycle;
- canonical request hashing and idempotency;
- project-scoped mutation serialization;
- path containment and sensitive-path policy;
- command-ID allowlists;
- approvals;
- audit;
- output bounding and redaction;
- Git checkpoints;
- branch/HEAD compare-and-swap checks;
- rollback;
- unknown-side-effect blocking and reconciliation;
- worker lifecycle and runtime diagnostics.

The Bridge is the only MCP server exposed to a remote transport.

### 3.3 codemcp is a pinned execution backend

Current baseline:

- upstream `codemcp==0.3.0`;
- pinned commit `683e6ec29b15b91ec12430afabf5a45ed57d2489`;
- upstream dependency remains unchanged;
- Bridge-owned Windows compatibility wrapper isolates native Windows subprocess/newline behavior;
- no permanent upstream fork is required by the current implementation.

The dependency baseline is documented in [`guides/codemcp-baseline.md`](guides/codemcp-baseline.md).

## 4. Public MCP Contract

The current public surface contains 22 tools:

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

The public contract must continue to exclude:

- arbitrary shell;
- caller-controlled executable paths;
- generic caller-controlled argv;
- arbitrary host paths;
- project registry mutation through MCP;
- automatic push, merge, rebase or deploy.

Schema changes require an explicit security and compatibility review.

## 5. Project Authorization Control Plane

Project registration is a **local administrative action**.

Normal flow:

```text
local CLI project add/remove
  -> validate candidate registry
  -> atomic projects.toml replacement
  -> running Bridge detects validated generation change
  -> authorization changes take effect without restart
```

Rules:

- MCP clients cannot add, remove, reload or reconfigure projects;
- live reload keeps a last-known-good registry;
- invalid registry updates fail closed;
- in-place project-root redirection is rejected;
- removal revokes new access and blocks affected active sessions;
- re-adding the same project ID does not revive old sessions;
- direct `projects.toml` editing is reserved for trusted offline maintenance or recovery.

## 6. Mutation and Git Safety Model

A mutation requires a caller-provided `client_request_id` and canonical SHA-256 `request_hash`.

The operation lifecycle is explicit:

```text
received
  -> validated
  -> awaiting_approval (when required)
  -> dispatched
  -> running
  -> succeeded | failed | cancelled | unknown
```

Required behavior:

- identical request identity replays the persisted result;
- same request ID with a different hash is rejected;
- one project has at most one active mutation;
- dirty-worktree policy is enforced before protected mutations;
- a Bridge-owned checkpoint is created before mutation;
- mutation records expected branch/HEAD and post-state evidence;
- unexpected branch/HEAD changes fail closed;
- rollback uses registered checkpoint refs plus CAS validation;
- uncertain backend outcomes become `unknown`;
- a blocked project requires explicit evidence-backed `operation_reconcile`;
- no uncertain mutation is transparently retried.

## 7. Current Completed Tracks

The following implementation tracks are already complete or substantially complete:

- core Bridge policy and MCP surface;
- SQLite session/operation/approval/audit persistence;
- Git checkpoint and CAS rollback;
- Native Windows worker;
- WSL2 compatibility fallback;
- local CLI project add/remove;
- project registry hot reload and revocation;
- Windows packaged EXE;
- Windows installer;
- release-candidate ZIP/checksum generation;
- DPAPI runtime secret storage;
- Cloudflare transport;
- Profile A network trust;
- optional Profile B OAuth Resource Server integration;
- public README and Windows install/use guide;
- AGPL-3.0-only licensing;
- security policy, security model and threat model;
- GitHub CI/governance files;
- Cloudflare network-trust Phase A-H live acceptance.

Phase H has demonstrated the real path:

```text
ChatGPT
-> Cloudflare network restriction
-> Tunnel
-> Bridge
-> registered project
-> mutation
-> identical replay
-> explicit approval
-> checkpoint/CAS restore
-> exact clean baseline
```

This evidence does **not** by itself approve stable `v0.1.0`.

## 8. Remaining v0.1.0 Work

### Phase 6 — Windows operations and reliability

Authoritative record:

[`acceptance/phase-6-validation.md`](acceptance/phase-6-validation.md)

Required before PASS:

- 20/20 packaged-runtime lifecycle cycles;
- Bridge/Tunnel/native-worker abnormal exit recovery;
- unrelated port/listener fail-closed behavior;
- stale state handling;
- Git prerequisite failure diagnostics;
- Tunnel credential failure without secret leakage;
- disconnect during mutation without transparent replay;
- timeout and owned process-tree cleanup;
- synthetic secret/log canary validation;
- spaces, Chinese paths, CRLF/LF and supported long paths;
- dependency upgrade/rollback review.

### Phase 7 — Final release acceptance

Authoritative record:

[`acceptance/acceptance-test-plan.md`](acceptance/acceptance-test-plan.md)

Required before PASS:

- final-RC automated suite, lint, format and build;
- exact 22-tool MCP contract verification;
- complete functional matrix;
- complete security negative matrix;
- restart/disconnect/unknown/reconcile reliability matrix;
- 10 full real-project remote modification tasks;
- ChatGPT-only reasoning-boundary verification.

### Open-source supply-chain gate

Required:

- tracked-tree secret scan;
- full Git-history secret scan;
- final artifact secret scan;
- dependency vulnerability audit;
- dependency license review;
- third-party notice decision.

### Documentation consistency gate

Current normative documents must agree on:

- Native Windows worker as default;
- WSL2 only as compatibility fallback;
- Cloudflare Profile A as recommended personal path;
- Secure MCP Tunnel as optional compatibility path;
- Profile B OAuth as optional advanced path;
- installed runtime does not require Python/uv/pwsh/WSL2;
- Git for Windows is a runtime prerequisite;
- network trust does not imply user identity;
- current project registry hot-reload semantics.

### Strict clean-machine packaging gate

The final release candidate must:

- be rebuilt from the final release commit;
- install on clean Windows 11;
- run with isolated product PATH without Python/uv/pwsh;
- use Native Windows worker;
- register a disposable project;
- pass real Connector read/mutation/replay/restore against that disposable project;
- restore exact Git baseline;
- pass cleanup/uninstall;
- pass final artifact secret scan;
- publish matching SHA-256.

### GitHub hosted gate

Repository-side CI/governance files already exist.

Still required:

- hosted Ubuntu and Windows CI PASS;
- PR required checks;
- Dependabot activation;
- branch/ruleset protection;
- issue/PR template rendering.

## 9. Execution Order From Current State

Proceed in this order:

1. align current normative documentation;
2. execute Phase 6 real-host matrix;
3. fix any Phase 6 blocker;
4. execute final-RC Phase 7 automated/functional/security/reliability gates;
5. execute 10 real-project tasks;
6. perform secrets/supply-chain/license audit;
7. perform strict clean-machine packaging acceptance;
8. activate and verify hosted GitHub CI/rulesets;
9. freeze the final release commit;
10. rebuild final installer and ZIP from that exact commit;
11. regenerate SHA-256 and release notes;
12. run final Release Gate sign-off;
13. tag `v0.1.0`;
14. publish GitHub Release.

Any change after a gate that affects runtime behavior, security semantics, dependency lock or release artifact invalidates the affected evidence and requires re-validation.

## 10. Non-Goals for v0.1.0

Do not add these merely to unblock the release:

- arbitrary shell;
- automatic push/merge/rebase/deploy;
- hidden local reasoning;
- Bridge-hosted agent loop;
- model provider integration;
- multi-user RBAC;
- broad platform expansion;
- new transports without a release-critical need;
- a codemcp fork without demonstrated compatibility need.

## 11. Release Decision Rule

Stable release remains:

```text
release_decision = BLOCKED
```

until every mandatory gate in the open-source readiness plan and final acceptance plan is backed by evidence from the final release candidate.

Implementation existence is not equivalent to release acceptance.
