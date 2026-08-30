# Security Model

## Purpose

codemcp-remote exposes controlled local development capabilities to ChatGPT through an MCP Bridge. Because those capabilities include source reads, source mutations, registered development commands, and limited Git recovery, the Bridge is the security enforcement point.

The design goal is not to make arbitrary remote shell access safe. The design goal is to avoid exposing arbitrary shell or arbitrary filesystem access at all.

The v0.1.0 remote surface has two explicit security profiles. Profile A is the recommended personal-deployment path: ChatGPT Connector uses `Authentication = No authentication`, Cloudflare WAF restricts the Connector egress network, and the Bridge applies exact Host/Origin and local policy checks. Profile B is the optional OAuth Resource Server path for deployments that require subject, client, and scope identity. Cloudflare IP restriction is a network trust boundary, not authentication or user identity.

## Architecture and trust boundaries

```text
ChatGPT Connector
  |
  | Authentication = No authentication (Profile A)
  v
OpenAI / ChatGPT Connector egress network
  |
  v
Cloudflare WAF IP allowlist
  |
  v
https://<mcp-host>/mcp
  |
  | Cloudflare Tunnel
  v
127.0.0.1:46200
  |
  v
codemcp-remote Bridge
  |
  | exact Host/Origin + project/tool invocation
  v
native local codemcp worker
  |
  v
registered local Git project
```

The OpenAI Secure MCP `tunnel-client` remains an optional compatibility transport. The Bridge must remain bound to `127.0.0.1:46200` for either remote transport.

### ChatGPT

ChatGPT is the only reasoning engine in the current architecture. It decides which MCP tool to call and what edit intent to submit.

ChatGPT is **not** trusted to bypass Bridge policy. A mistaken instruction, prompt injection from repository content, or compromised conversation must still be constrained by project registration, relative-path validation, command registration, mutation preconditions, approvals, and Git compare-and-swap checks.

For Profile A, the Cloudflare egress allowlist does not identify the ChatGPT user, Workspace, account, or conversation. The internal `network-chatgpt-v1` principal is a deterministic network-only audit/replay identity, not a human identity.

### Cloudflare WAF network boundary

The Cloudflare IP List and whole-host WAF rule are the IP enforcement boundary. They must be configured at Cloudflare Edge before Tunnel ingress, for example:

```text
(http.host eq "mcp.example.com" and not ip.src in $chatgpt_connectors)
```

The current OpenAI Connector ranges are deployment state and are intentionally not hardcoded in this repository. The Bridge does not authorize from `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, or `Cf-Access-*`. A WAF `ALLOW` therefore means only network admission; it is not authentication.

### Remote transport boundaries

The Tunnel is a transport boundary. The recommended Cloudflare profile forwards to the Bridge's loopback MCP endpoint through a WAF-restricted hostname. The optional OpenAI Secure MCP profile uses outbound connectivity to the OpenAI control plane.

Neither Tunnel grants project authorization or replaces the Bridge's Host/Origin, approval, session, operation, replay, or audit checks. The Bridge must remain safe if a caller sends policy-violating MCP input through an otherwise valid transport.

### Bridge

The Bridge is the primary security boundary. It is responsible for:

- resolving only registered `project_id` values;
- accepting only project-relative authorized paths;
- rejecting sensitive paths;
- rejecting symlink, junction, and reparse-point traversal;
- exposing fixed tools instead of arbitrary shell;
- executing only registered commands with bounded timeouts;
- enforcing mutation preconditions and per-project mutation serialization;
- binding operations to session/project state;
- validating canonical request hashes for mutation idempotency;
- issuing and consuming short-lived one-time approvals;
- recording operation/audit state;
- creating Bridge-owned Git checkpoints;
- performing compare-and-swap rollback;
- preserving `unknown` when a side effect cannot be established safely;
- bounding and sanitizing tool output.

The default example configuration binds to `127.0.0.1:46200`, denies arbitrary paths, arbitrary commands, and model calls, requires a clean workspace, and sets `model_egress = "deny"`.

### codemcp worker

`codemcp==0.3.0` is a pinned third-party execution backend. The packaged Windows profile uses the native local worker by default; WSL2 remains a source-mode compatibility fallback.

The worker is not a second reasoning agent. The Bridge must not assume that a backend error proves no local side effect occurred. Where completion cannot be determined, the operation must become `unknown` and require reconciliation.

The upstream `codemcp 0.3.0` distribution has inconsistent license metadata: its package `METADATA` says `MIT`, while the `LICENSE.txt` shipped in the same distribution is Apache-2.0. The release preserves the bundled license text and records the discrepancy in third-party notices; final license review remains a release gate.

### Registered project and repository content

A project root is authorized by local operator configuration. Files inside the repository are data, not policy authority.

Repository text can contain malicious instructions or prompt injection. Such content must not:

- register another project;
- widen allowed paths;
- add arbitrary runtime arguments;
- approve an operation;
- disable audit;
- change the meaning of an approval token;
- authorize destructive Git actions.

Project configuration that controls executable commands is security-sensitive and must be treated as operator-controlled configuration, not as permission derived from repository prose.

### Local project-authorization control plane

Project registration is intentionally **not** an MCP capability. The local CLI is the administrative control plane for adding and removing authorized project roots. The remote MCP execution plane can only operate on projects that the local operator has already registered.

A running Bridge watches only the validated `projects.toml` registry. On an authorization-sensitive request it can atomically install a newly validated snapshot without restarting the Bridge, Tunnel, or ChatGPT Connector. This hot reload does not extend to `remote.toml`, authentication, network trust, Tunnel configuration, or other global security settings.

Removal is a revocation boundary: new access is denied and active sessions for the removed project are persistently blocked. Re-adding the same project ID does not revive those old sessions.

An in-place root change for an existing `project_id` is rejected and the last-known-good snapshot remains active. Moving an ID to another root requires an explicit remove followed by add, with the removal observed first. Invalid or racing registry candidates also preserve the last-known-good snapshot.

Direct `projects.toml` editing is trusted local maintenance, not a remote administration mechanism. Normal authorization changes should use the local CLI so validation and atomic replacement are preserved.

### Local operating-system account

The local OS user is a root trust assumption for the current version. An attacker who can arbitrarily modify the packaged Bridge executable/runtime home, local configuration, SQLite database, trusted scripts, Git executable, optional source-development Python/WSL environments, or runtime process memory is outside the protection boundary.

codemcp-remote is not a sandbox against a compromised local administrator/user account.

## Filesystem authorization

### Registered roots only

The public tool surface accepts a `project_id`, not an arbitrary host path. Unknown IDs are rejected.

Paths are normalized as project-relative paths. Resolution must remain below the registered project root.

### Link escape prevention

Existing path components are checked for symbolic links and Windows reparse points. A path that traverses such a component is rejected rather than followed.

This protects the intended repository-root boundary but is not a substitute for OS-level sandboxing.

### Sensitive-path denial

The current deny rules include names such as `.git`, credentials/password/secret/token names, common private-key/certificate suffixes, and `*.env` / `*.env.*`.

Search traversal also excludes sensitive paths before invoking the backend, and results are filtered again before return.

The deny list is defense in depth, not a guarantee that every secret can be recognized by filename. Users must not store high-value secrets in source repositories merely because this filter exists.

## Command execution

The Bridge does not accept arbitrary executable paths, shell strings, or caller-supplied argv for registered commands.

Commands are resolved by ID from operator-controlled configuration. Known lower-risk development command kinds can run without an extra approval; unknown or higher-risk kinds default to explicit approval. Commands have bounded timeouts.

The Bridge does not expose push, merge, rebase, deploy, branch deletion, force reset, or arbitrary Git arguments as generic MCP capabilities.

A registered command itself can still be dangerous if the operator configured it dangerously. Command registration is therefore part of the trusted local policy surface.

## Mutation safety

### Clean baseline

The default policy requires:

- an allowed branch;
- a clean Git worktree;
- a recorded branch/HEAD baseline;
- serialized mutation for a project.

### Idempotency

Mutation calls require a caller request ID and SHA-256 request hash. The Bridge independently computes a canonical hash of security-relevant operation input and rejects mismatches.

A previously used request ID cannot safely be repurposed for a different mutation.

### Approval

High-risk operations use a random, short-lived approval token. Only its hash is persisted. Approval is operation-bound, expires, and is one-time consumable.

An approval is not a general capability token for another session, project, or action.

### Checkpoints and rollback

Before mutation, the Bridge records Git state and creates a Bridge-owned checkpoint ref.

Rollback is compare-and-swap:

1. identify a registered checkpoint belonging to the current project/session;
2. verify its ref;
3. require the expected current HEAD;
4. recheck branch/HEAD and clean worktree;
5. require explicit approval;
6. create a rollback safety checkpoint;
7. execute the fixed restore path.

If external Git state changes, rollback fails closed instead of overwriting the newer state.

### Unknown side effects

Network loss, process crashes, backend rejection, timeout, or cleanup failure can leave uncertainty about whether a mutation occurred.

When the Bridge cannot prove the outcome, `unknown` is a valid terminal/recovery state. The project must be reconciled before unsafe replay. Availability is secondary to avoiding duplicate or destructive mutation.

## Audit and secret handling

Operations, approvals, checkpoints, and audit events are persisted in SQLite. The database is intended to store metadata, hashes, bounded summaries, and errors rather than complete source-file snapshots.

Plaintext approval tokens must not be persisted. Runtime Tunnel credentials must not be committed to the repository or printed in diagnostics.

Logs and tool results are still potential disclosure paths; release validation must include explicit secret/log scanning.

## Network model

The Bridge listens only on loopback. The recommended public path is:

```text
ChatGPT Connector
  -> OpenAI Connector egress network
  -> Cloudflare WAF IP List/rule
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200
  -> exact Host/Origin network-trust middleware
  -> MCP transport and existing project/security policies
```

Profile A uses `auth.mode = "none"` only with `network_trust.mode = "cloudflare-chatgpt"` and non-empty exact `allowed_hosts`. It does not install a bearer authenticator and accepts a valid `/mcp` request without `Authorization`. This does not remove any project, path, command, operation, approval, checkpoint, Git CAS, restore, replay, or audit policy. Profile B continues to validate OAuth Resource Server credentials and exposes subject/client/scope identity semantics.

The example policy denies model egress from the Bridge. `/healthz` is a lifecycle probe and should not be exposed as a public application endpoint. Its project-registry observability is deliberately sanitized to counts, generation, reload status, and stable error codes; it does not expose project IDs, roots, command argv, or raw configuration errors.

This is an application policy, not a host firewall. A compromised dependency or local process can violate assumptions unless separately constrained by the OS/network environment.

## Current non-guarantees

The initial `v0.1.0` target does not guarantee:

- protection against a compromised local OS user or administrator;
- multi-user identity, tenancy isolation, or RBAC;
- live Cloudflare account/WAF configuration or ChatGPT Connector availability;
- recognition of every possible secret filename/content;
- containment of an arbitrary malicious registered command;
- security against a malicious replacement of the pinned dependencies or local toolchain;
- automatic recovery from every `unknown` mutation;
- availability when the selected Tunnel, Git, codemcp, or optional WSL2 fallback is unhealthy;
- safety of unsupported transport adapters.

Any future capability that expands filesystem scope, executable scope, identity scope, Git behavior, or remote transport must update this document, the threat model, and regression tests before release.
