# codemcp-remote Architecture Baseline

> Updated: 2026-08-28  
> Release target: `v0.1.0`  
> Status: **CURRENT NORMATIVE ARCHITECTURE**

This document describes the current architecture. Historical Phase 0-5 records under `docs/releases/` and historical validation reports under `docs/reports/` remain evidence of earlier states and must not override this baseline.

## 1. Architectural Goal

codemcp-remote lets ChatGPT operate on explicitly registered local Git repositories through a constrained MCP surface while keeping ChatGPT as the only reasoning engine.

The architecture deliberately separates:

- reasoning;
- remote transport;
- network/auth boundary;
- local policy enforcement;
- execution;
- Git safety;
- persistence and audit.

The Bridge is not an agent and codemcp is not directly exposed to ChatGPT.

## 2. Recommended v0.1.0 Topology

The recommended single-operator personal deployment is Profile A:

```text
ChatGPT
  -> ChatGPT Connector
     Authentication = No authentication
  -> OpenAI / ChatGPT Connector egress network
  -> Cloudflare Edge / WAF IP allowlist
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200/mcp
  -> codemcp-remote Bridge
  -> Native Windows codemcp worker
  -> registered local Git repository
```

The installed Windows release includes the Bridge, Native Windows worker integration and `cloudflared`.

Git for Windows is a runtime prerequisite.

Normal installed operation does not require Python, `uv`, PowerShell 7 or WSL2.

## 3. Optional Compatibility Profiles

### 3.1 Profile B — OAuth Resource Server

For deployments that require subject/client/scope identity:

```text
ChatGPT / MCP client
  -> authorization flow
  -> Cloudflare Tunnel
  -> codemcp-remote OAuth Resource Server boundary
  -> Bridge policy
  -> registered project
```

Profile B uses:

```toml
[auth]
mode = "oauth-resource-server"
```

It retains the existing `mcp-rs-verification-v1` and RFC 9728 Resource Server behavior.

Profile B is optional for `v0.1.0` and does not redefine the default personal deployment.

### 3.2 OpenAI Secure MCP Tunnel

The OpenAI `tunnel-client` transport remains available as an optional compatibility transport.

It does not replace Bridge authorization and it is not the recommended personal public path for `v0.1.0`.

### 3.3 WSL2 worker

WSL2 Ubuntu remains an explicit source-mode compatibility fallback:

```toml
[codemcp]
worker_mode = "wsl2"
```

Native Windows is the default worker mode.

## 4. Core Trust Boundaries

### 4.1 ChatGPT

Trusted for:

- user-request interpretation;
- planning;
- deciding which MCP tool to invoke next;
- generating intended edits and parameters.

Not trusted to bypass:

- project registration;
- path policy;
- command policy;
- approval;
- operation identity;
- Git safety;
- network/auth policy.

Repository content may influence ChatGPT reasoning, but repository text alone can never authorize a privileged Bridge operation.

### 4.2 Cloudflare network trust

Profile A uses Cloudflare WAF/IP allowlisting as a network provenance restriction.

It means only:

> the request reached Cloudflare from an operator-configured OpenAI/ChatGPT Connector egress range.

It does **not** prove:

- human user identity;
- ChatGPT account identity;
- Workspace identity;
- conversation identity;
- authorization to a local project.

Profile A reports:

```text
identity_level = network-only
principal = network-chatgpt-v1
```

The Bridge must not authorize from:

- `X-Forwarded-For`;
- `Forwarded`;
- `CF-Connecting-IP`;
- `True-Client-IP`;
- `Cf-Access-*`.

Network admission is enforced at Cloudflare Edge before Tunnel ingress.

### 4.3 Bridge

The Bridge is the authoritative local security and lifecycle boundary.

It owns:

- project authorization;
- path containment;
- sensitive-path denial;
- command allowlists;
- session ownership;
- operation state;
- canonical request hashing;
- idempotency;
- project mutation locks;
- approvals;
- audit;
- output bounding/redaction;
- worker lifecycle;
- Git checkpoints;
- branch/HEAD CAS;
- rollback;
- unknown-side-effect handling;
- reconciliation.

The Bridge listens only on loopback.

### 4.4 codemcp worker

codemcp is an execution backend.

Current baseline:

- `codemcp==0.3.0`;
- pinned upstream commit `683e6ec29b15b91ec12430afabf5a45ed57d2489`;
- no model provider;
- no independent reasoning loop;
- no direct public exposure.

The Native Windows compatibility layer belongs to the Bridge and does not modify the upstream codemcp package.

### 4.5 Git repository

A registered Git repository is the mutation safety boundary.

The Bridge requires the registered project root to match the intended Git worktree root for protected Git operations.

Git is used for:

- clean-worktree validation;
- before-state identity;
- checkpoints;
- changed-file evidence;
- bounded diff evidence;
- CAS rollback;
- reconciliation.

Git is not an implicit publication mechanism.

The Bridge does not automatically push, merge, rebase, deploy or delete branches.

## 5. Installed Runtime Boundary

The packaged Windows product is self-contained except for Git for Windows.

Typical installed components:

```text
codemcp-remote.exe
codemcp-start.cmd
codemcp-stop.cmd
cloudflared.exe
tunnel-client.exe        # optional compatibility transport
config/
data/
secrets/
```

Runtime home precedence is:

```text
--home
  -> CODEMCP_HOME
  -> packaged EXE directory
```

Runtime secrets may be stored with Windows DPAPI.

Plaintext transport or verification credentials must not be committed to the repository, copied into example config or persisted in unrestricted logs.

## 6. Project Registry Architecture

Project registration is intentionally outside the MCP surface.

### 6.1 Administrative path

```text
local CLI
  -> project add/remove
  -> validate complete registry candidate
  -> atomic projects.toml replacement
  -> generation change
  -> running Bridge reloads on authorization-sensitive request
```

### 6.2 Security properties

The registry implementation must preserve:

- local-only administration;
- canonical project roots;
- last-known-good snapshot;
- fail-closed parse/validation behavior;
- in-place root-redirect rejection;
- removal revocation;
- blocking of affected active sessions;
- no resurrection of old sessions after re-add;
- sanitized status/doctor observability.

MCP clients cannot:

- add projects;
- remove projects;
- reload the registry;
- mutate project roots;
- alter registered command policy.

## 7. MCP Surface

The current public contract contains 22 tools:

```text
project_open
project_status
file_read
code_search
file_list
file_edit
file_create
file_write
file_move
file_delete
directory_create
registered_command_run
format_run
test_run
git_status
git_diff
checkpoint_create
checkpoint_restore
operation_status
approval_confirm
operation_cancel
operation_reconcile
```

The API is deliberately narrower than remote shell access.

It does not expose:

- arbitrary executable paths;
- arbitrary shell text;
- arbitrary argv extension;
- arbitrary host filesystem roots;
- raw Git reset;
- push/merge/rebase/deploy;
- project-administration tools.

## 8. Session and Operation Persistence

The Bridge persists state in SQLite.

Core records include:

```text
sessions
operations
approvals
checkpoints
audit_events
```

The database stores bounded operational metadata, not complete source-file snapshots.

Plaintext approval tokens are not persisted.

### 8.1 Operation state

```text
received
  -> validated
  -> awaiting_approval
  -> dispatched
  -> running
  -> succeeded | failed | cancelled | unknown
```

Read operations are audited but do not use mutation replay semantics.

Mutation operations require:

- `client_request_id`;
- canonical SHA-256 `request_hash`.

A request hash is recomputed by the Bridge over the canonical operation input.

A caller cannot authorize different arguments by supplying an unrelated digest.

### 8.2 Replay isolation

Identical request identity and canonical hash return the persisted result.

A reused request ID with different canonical input fails with an idempotency conflict.

Replay identity is scoped by the active auth/network-trust context so incompatible principals/profiles do not silently share mutation replay state.

## 9. Mutation Serialization and Unknown Outcomes

Only one active mutation is allowed per project by default.

A mutation crosses explicit safety stages:

```text
validate session/project
  -> acquire project mutation lock
  -> inspect clean Git baseline
  -> create Bridge checkpoint
  -> execute bounded operation
  -> verify branch/HEAD/post-state
  -> finalize checkpoint/audit
```

If the Bridge cannot establish whether a side effect occurred, it must not guess.

The operation becomes:

```text
unknown
```

and unsafe follow-up mutations remain blocked until `operation_reconcile` establishes evidence.

Transparent mutation replay after disconnect/crash is prohibited.

## 10. Git Checkpoint and CAS Restore

Mutation checkpoints use Bridge-owned refs:

```text
refs/codemcp-remote/checkpoints/<checkpoint_id>
```

A checkpoint stores:

- project;
- branch;
- before HEAD;
- ref identity;
- before file manifest metadata;
- after HEAD when known;
- changed files;
- bounded diff hash;
- operation relationship.

`checkpoint_restore` is a high-risk approved mutation.

Before running the fixed reset, the Bridge verifies:

- checkpoint belongs to the project/session scope;
- checkpoint ref is registered and valid;
- current worktree is clean;
- current branch matches;
- caller-supplied expected HEAD matches;
- current HEAD still matches the expected value.

External Git changes cause fail-closed conflict.

An uncertain Git result becomes `UNKNOWN_SIDE_EFFECT` rather than a guessed success.

## 11. Registered Commands

Commands are configured locally by command ID.

ChatGPT can choose only a registered ID.

The Bridge does not accept arbitrary runtime argv from MCP callers.

Registered commands are bounded by:

- fixed executable/argv;
- timeout;
- output limits;
- project root;
- optional approval;
- Git before/after checks;
- process-tree ownership.

If a registered command unexpectedly changes branch or HEAD, the operation becomes unknown/fail-closed according to the operation contract.

## 12. Logging and Secret Handling

Runtime logs are local sensitive operational data.

Requirements:

- redact common API-key/Bearer/token forms;
- do not persist plaintext approval tokens;
- do not log complete denied secret-file contents;
- bound worker stderr and command output;
- keep runtime data Git-ignored;
- treat validation evidence as sensitive even after redaction.

Profile A normally stores the Cloudflare Tunnel token with Windows DPAPI.

Profile B may also store its Resource Server verification secret with DPAPI.

## 13. Lifecycle and Packaging

The installed product provides managed lifecycle commands:

```text
codemcp-remote.exe doctor
codemcp-remote.exe start
codemcp-remote.exe status
codemcp-remote.exe stop
```

It also packages one-click start/stop launchers.

Release artifacts currently include:

- Windows EXE payload;
- Windows installer;
- release-candidate ZIP;
- SHA-256 manifests/checksums.

These implementation capabilities are not equivalent to a stable release PASS.

The final `v0.1.0` artifact must still pass the strict clean-machine release gate from the exact final release commit.

## 14. Current Release Evidence Boundary

Cloudflare Profile A Phase A-H live acceptance is complete.

It has demonstrated:

- real ChatGPT Connector access;
- full 22-tool discovery;
- registered project access;
- deterministic mutation;
- identical replay;
- explicit approval;
- checkpoint/CAS restore;
- exact clean-baseline recovery;
- ordinary public source blocked by Cloudflare;
- ChatGPT Connector source allowed.

Stable `v0.1.0` remains blocked by the separate repository-wide release gates:

- Phase 6 Windows operations/reliability;
- Phase 7 final acceptance;
- real-project task matrix;
- secrets and supply-chain audit;
- current-document consistency;
- strict clean-machine packaging;
- hosted GitHub CI/ruleset activation.

## 15. Deliberate Non-Goals

The current architecture does not provide:

- arbitrary shell;
- hidden background agent reasoning;
- Bridge model calls;
- automatic code publication;
- automatic deploy;
- multi-user RBAC;
- strong human identity in Profile A;
- guaranteed protection from a compromised local OS user;
- guaranteed protection from a compromised ChatGPT account/workspace;
- arbitrary filesystem administration.

## 16. Normative References

Current behavior should be interpreted in this order:

1. current code and tests;
2. this architecture baseline;
3. [`../implementation-plan.md`](../implementation-plan.md);
4. [`../plans/v0.1.0/open-source-readiness-plan.md`](../plans/v0.1.0/open-source-readiness-plan.md);
5. current guides under `docs/guides/`;
6. current acceptance plans under `docs/acceptance/`.

Files under `docs/releases/` and `docs/reports/` are historical evidence and may describe an earlier supported path.
