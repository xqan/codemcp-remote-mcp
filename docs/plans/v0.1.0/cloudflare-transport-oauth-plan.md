# Phase 5.5 — Cloudflare Transport + Network Trust + Optional OAuth

Status: **PHASE H COMPLETE — NETWORK TRUST TRACK CLOSED — the recommended 5.5.7A No-Auth + Cloudflare network-trust path passed real ChatGPT tool discovery, project access, mutation, replay, approval, checkpoint/CAS restore, exact clean-baseline recovery, Cloudflare BLOCK/ALLOW evidence, and the final registered gates (`316 passed, 6 skipped`; 72 files formatted). Stable `v0.1.0` publication remains BLOCKED by the separate repository-wide Phase 7 acceptance plan.**

Target release: `v0.1.0`

Repository: `codemcp-remote`

External dependency: a separately developed, general-purpose `mcp-auth-server` project.

Dependency status: `mcp-auth-server` Phase 4.1 has FROZEN Resource Server Verification Contract v1 (`mcp-rs-verification-v1`), and repository-side OAuth Resource Server integration through Phase 5.5.6 is complete. That implementation is retained as the optional advanced OAuth profile. The earlier Phase 5.5.7 RFC 9728 metadata/challenge and managed reinstall fixes remain historical acceptance evidence; they are not invalidated by the new personal-deployment profile. The live OAuth staging issuer and Cloudflare-published MCP resource were validated against operator-owned staging endpoints; exact historical endpoint values remain only in historical acceptance evidence.

The sections below retain historical Phase 5.5 implementation evidence. The Phase A contract in the next section is authoritative for the remaining work: Cloudflare network trust is the recommended single-user path, while OAuth Resource Server interoperability remains an optional advanced/enterprise path.

## Current accelerated closeout status

This section supersedes earlier “not started” statements retained in the historical sections below.

| Phase | Status | Evidence |
| --- | --- | --- |
| A | COMPLETE | Read-only architecture/config/security review; commit `b8c7531` |
| B | COMPLETE | Independent auth/network-trust models and fail-closed config validation; commit `f2209d3` |
| C | COMPLETE | Exact Host/Origin boundary, network-trusted principal, and OAuth replay/session isolation; commit `8481995` |
| D | COMPLETE | `--home`/`CODEMCP_HOME`, profile-aware CLI/doctor/status, Cloudflare public-start gate; commit `4761621` |
| E | COMPLETE | Clean Windows 5.5.7A/5.5.7B acceptance profiles and DPAPI/home wiring; commit `c74a639` |
| F | COMPLETE | Cloudflare deployment/WAF boundary is external operator state; this closeout does not call the Cloudflare API, hardcode IP ranges, or duplicate live provisioning |
| G | COMPLETE | Documentation alignment, installer smoke impact review, full local regression/lint/compile evidence; current closeout commit is recorded with this update |
| H | COMPLETE | Real `codemcp-557` No-Auth connector reached the Bridge and passed project access, 22-tool discovery, mutation, identical replay, explicit approval, checkpoint/CAS restore, exact clean-baseline recovery, plus Cloudflare ordinary-source `Block` and ChatGPT-source `Allow` evidence. Optional `1033` proof was skipped; release freeze remains pending. |

### Recommended v0.1.0 personal profile

```text
ChatGPT Connector (Authentication = No authentication)
  -> OpenAI/ChatGPT Connector egress network
  -> Cloudflare Edge/WAF IP List + whole-host block rule
  -> https://mcp.example.com/mcp
  -> Cloudflare Tunnel
  -> 127.0.0.1:46200
  -> codemcp-remote network-trusted Bridge
  -> project / operation / approval / checkpoint / Git CAS / restore / audit policy
```

Profile A is configured as:

```toml
[auth]
mode = "none"

[network_trust]
mode = "cloudflare-chatgpt"
allowed_hosts = ["mcp.example.com"]
allowed_origins = ["https://chatgpt.com"] # optional, if-present validation
```

Cloudflare’s IP allowlist is a `network trust boundary` / `ChatGPT egress network restriction`, not authentication, user identity, or strong identity. It proves only that the request arrived from a configured OpenAI/ChatGPT Connector egress range; it does not identify a ChatGPT user, Workspace, account, or conversation. The Bridge never authorizes from `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, or `Cf-Access-*`.

Profile B remains available for multi-user, enterprise, or subject/scope-aware deployments:

```toml
[auth]
mode = "oauth-resource-server"
issuer = "https://<authorization-server>"
canonical_resource_uri = "https://<mcp-host>/mcp"
validation_resource_id = "<resource-id>"
```

Profile B retains `mcp-rs-verification-v1`, RFC 9728 metadata/challenges, OAuth subject/client/scope semantics, and the independent `mcp-auth-server` boundary. OAuth was moved to an optional advanced profile; it was not abandoned or deleted.

### Current implementation contract

- `auth.mode = "none"` is persisted explicitly and is valid only with `network_trust.mode = "cloudflare-chatgpt"` and non-empty canonical `allowed_hosts`.
- Missing/invalid trust policy, unknown auth/trust modes, or Cloudflare public No-Auth without trust fails closed with `PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST`.
- Host policy is exact hostname matching; runtime `:443` canonicalization is implemented in the request boundary. Origin is validated only when present.
- Profile A does not install an OAuth bearer authenticator and reports `identity_level = network-only`.
- Profile A uses the deterministic internal principal/replay namespace `network-chatgpt-v1`; OAuth uses a separate `oauth-<digest>` namespace. Neither claims to identify a human user.
- Bridge and Tunnel remain loopback-bound; Cloudflare ingress must target `127.0.0.1:46200` and `/healthz` must not be exposed as an unintended public endpoint.
- `--home` takes precedence over `CODEMCP_HOME`; legacy `--app-root` remains supported for existing installations. Runtime data, checkpoints, logs, and secrets are derived from the selected home without destructive migration.

### Local evidence and live boundary

The accelerated closeout validates source-mode behavior, package/harness contracts, PowerShell parsing, installer script impact, full Python regression, full Ruff check/format validation, and `compileall`. On 2026-08-26 the complete local suite reported `312 passed, 6 skipped`; the skipped cases are environment/profile-specific and are recorded in the acceptance plan.

It does not prove that a real Cloudflare WAF rule currently allows ChatGPT Connector traffic, blocks an ordinary public IP, or that a real ChatGPT Connector can complete the remote contract. Those are Phase H live-only checks and must be recorded with non-sensitive evidence before any release freeze.

The deployment runbook is [`docs/guides/cloudflare-tunnel-setup.md`](../../guides/cloudflare-tunnel-setup.md). It covers manual IP List/WAF provisioning, Profile A initialization, negative testing, Profile B, rollback, and the explicit limitation that IP allowlisting does not identify an individual ChatGPT user.

## Phase A — Architecture/config/security review

**Review date:** 2026-08-26
**Review branch:** `codex/phase-a-network-trust-review`
**Review baseline:** repository HEAD `65853c0` before the review-record commit
**Scope:** read-only inspection of repository rules, runtime configuration, authentication, transport, lifecycle, replay/session ownership, tests, Windows acceptance harness, and current documentation.
**Implementation status:** no runtime source, test, deployment configuration, secret, Cloudflare account state, or live endpoint was changed during this review. At review time, Phase B had not started; subsequent phase results are recorded below.

### A.1 Executive conclusion

The repository already has two useful foundations:

- Cloudflare Tunnel publishes an origin that is validated as `127.0.0.1` and keeps the Bridge loopback-only.
- The OAuth Resource Server path is implemented against the frozen `mcp-rs-verification-v1` contract, including RFC 9728 metadata/challenge behavior, safe principal audit propagation, and authenticated idempotency checks.

The requested recommended personal deployment is not yet implementable. The current lifecycle explicitly rejects Cloudflare startup when no OAuth authenticator is configured, and there is no `network_trust` configuration, exact Host/Origin middleware, or network-trusted synthetic principal. The current 5.5.7 harness is also OAuth-first and requires `CODEMCP_RS_VERIFICATION_SECRET`.

The target must therefore be modeled as two explicit profiles:

| Profile | Runtime configuration | Security meaning | Intended deployment |
| --- | --- | --- | --- |
| `network-trusted` | `auth.mode = "none"` plus `network_trust.mode = "cloudflare-chatgpt"` and non-empty exact `allowed_hosts` | Network trust boundary only; `identity_level = network-only` | Single-user self-hosting, private ChatGPT Connector, Cloudflare Tunnel/WAF |
| `oauth-resource-server` | `auth.mode = "oauth-resource-server"` plus the existing issuer/resource/contract settings | Real subject/client/scope identity supplied by the external authorization profile | Multi-user, enterprise, or deployments requiring user identity |

Cloudflare’s IP allowlist must never be described as authentication, user identity, or strong identity. It proves only that the request arrived from an allowed OpenAI/ChatGPT Connector egress range. It does not identify a ChatGPT user, Workspace, account, or conversation. The OAuth profile is the only profile in this design that provides subject/client/scope identity semantics.

### A.2 Evidence reviewed

The review traced the following implementation and acceptance surfaces:

- `bridge/src/codemcp_bridge/lifecycle.py`: versioned `remote.toml` provider/auth persistence, authenticator loading, Cloudflare startup gate, doctor/status, DPAPI secret handling, and process ownership.
- `bridge/src/codemcp_bridge/resource_auth.py`: `mcp-rs-verification-v1`, `AuthenticatedPrincipal`, Bearer handling, RFC 9728 metadata, and auth context binding.
- `bridge/src/codemcp_bridge/mcp_transport.py` and `mcp_server.py`: request-authenticator hook, `/mcp`, `/healthz`, metadata route, and existing service policy path.
- `bridge/src/codemcp_bridge/settings.py` and `transports/cloudflare.py`: loopback constraints, public/origin URL validation, and Cloudflare process configuration.
- `bridge/src/codemcp_bridge/operation_service.py`, `session_service.py`, `db/schema.py`, and `db/store.py`: idempotency, audit context, fixed `local-policy` storage ownership, session binding, and checkpoint/CAS ownership checks.
- `bridge/tests/test_phase55_auth_configuration.py`, `test_phase55_oauth_resource_server.py`, `test_phase55_cloudflare_transport.py`, `test_phase55_security_gate.py`, and `test_phase557_clean_windows_harness.py`.
- `scripts/validate-clean-windows-release.ps1`, the current Cloudflare and external-auth guides, the security/threat model, and this Phase 5.5 plan.

### A.3 Existing controls that must remain unchanged

The review found no reason to weaken or remove the existing project/security boundaries. Subsequent phases must preserve:

- registered project allowlists and project/session binding;
- sensitive-path filtering and fixed command allowlists;
- mutation serialization, request-hash validation, replay semantics, and unknown-side-effect handling;
- approval, checkpoint, Git CAS, restore, audit, and clean-worktree policy;
- model egress denial;
- loopback Bridge and Cloudflare origin binding at `127.0.0.1:46200`.

The source does not currently implement Cloudflare IP enforcement in Python and does not hardcode OpenAI Connector ranges. That is the correct boundary. IP enforcement must be configured at Cloudflare Edge/WAF before the request can reach the Tunnel; it must not be recreated with request headers inside `codemcp-remote`.

### A.4 Findings and gaps

#### P0 — Cloudflare + No Auth cannot currently start

`lifecycle.start_services()` calls `load_request_authenticator()` and rejects Cloudflare when it returns `None` with an OAuth-only error. `doctor_report()` reports the same state as failed. This directly blocks Profile A and also causes the current clean Windows harness to require an OAuth verification secret.

#### P0 — The requested configuration model is absent

`BridgeSettings` has no `AuthSettings` or `NetworkTrustSettings`. The versioned `remote.toml` reader/writer supports only the OAuth Resource Server table; `configure_resource_auth(mode="none")` removes the auth table instead of persisting an explicit `auth.mode = "none"`, and no trust policy is persisted. Missing trust policy therefore cannot yet be distinguished and fail-closed for a public Cloudflare deployment.

Phase B should make the versioned runtime configuration explicit, preferably retaining `remote.toml` as the lifecycle-owned canonical file:

```toml
[auth]
mode = "none"

[network_trust]
mode = "cloudflare-chatgpt"
allowed_hosts = ["mcp.example.com"]
allowed_origins = ["https://chatgpt.com"] # optional if-present validation
```

The existing OAuth fields remain in `[auth]` for `oauth-resource-server`. Unknown auth/trust modes and unknown security fields must fail closed. `auth.mode = "none"` without a valid Cloudflare network-trust policy must not be accepted for public Cloudflare startup. Pure loopback development must remain separately distinguishable from a public Cloudflare transport.

#### P0 — Host/Origin enforcement and request context are missing

The current transport manager exposes a generic authenticator hook but has no network-trust middleware. The Bridge does not currently perform exact, case-insensitive Host canonicalization or optional Origin validation. It must not trust `X-Forwarded-Host`, `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, or `Cf-Access-*` as runtime authorization evidence.

The required Host policy is exact hostname matching after canonicalization: `mcp.example.com` and `mcp.example.com:443` are accepted; localhost, loopback, other hosts, subdomains, suffix attacks, wildcard entries, and `X-Forwarded-Host` overrides are rejected. Origin is an if-present check: missing Origin is accepted, exact `https://chatgpt.com` and `https://chatgpt.com:443` are accepted, and HTTP, subdomain, suffix-attack, `null`, or malformed origins are rejected.

#### P0 — Replay and session identity need a common auth-context design

The current `OperationService` stores OAuth details as an `auth.context` audit event and compares only the OAuth tuple `(contract_version, issuer, resource, subject, client_id)` during idempotent lookup. The database idempotency key remains `(project_id, session_id, client_request_id)`, and operation/session storage ownership is always `local-policy`. `local-policy` is a daemon/storage owner, not a caller identity.

There is no network-trusted principal today. The implementation must not use `client_id = "anonymous"` or let empty/no-auth context share the OAuth replay namespace. Phase C must introduce a common auth context with an explicit type and namespace, for example:

```text
network-trusted:
  auth_type = network-trusted
  principal = chatgpt-egress
  issuer = cloudflare-waf
  resource = configured canonical MCP resource
  identity_level = network-only
  replay_namespace = network-chatgpt-v1

oauth-resource-server:
  auth_type = oauth
  replay_namespace = oauth-<stable-digest>
```

The exact serialization must follow existing delimiter/identifier constraints. OAuth and network-trusted contexts must be distinct in audit, replay, operation ownership, and session binding. The migration must preserve existing records and must not reinterpret old OAuth records as network-trusted records.

#### P1 — `/healthz` is currently registered on the public-capable Bridge app

`mcp_server.py` registers `/healthz` on the same ASGI application that serves `/mcp`. The lifecycle probes it through loopback, but the current Tunnel/public routing does not by itself prove that it is inaccessible at the public hostname. The later deployment design must keep `/healthz` loopback/lifecycle-only and separately verify its public behavior. The WAF rule should protect the dedicated hostname as a whole, while public exposure of `/healthz` must not be assumed safe merely because `/mcp` is protected.

#### P1 — WAF/IP provisioning is external and undocumented as the new boundary

The repository contains no OpenAI Connector IP ranges, which is correct, but it also has no authoritative runbook for the Cloudflare IP List and WAF rule requested by Profile A. Phase F must document:

1. a Cloudflare IP List named `chatgpt_connectors` populated from the official OpenAI Connector egress manifest;
2. a whole-host block rule equivalent to `(http.host eq "mcp.example.com" and not ip.src in $chatgpt_connectors)`;
3. the fact that this is a network restriction, not user authentication;
4. manual provisioning first if the official manifest contract is not stable enough for a parser;
5. optional future sync tooling with strict schema/CIDR validation, non-empty/diff/dry-run checks, environment-only scoped API credentials, and no runtime dependency on Cloudflare credentials.

No Cloudflare API call or account change is authorized in Phase A.

#### P1 — Clean Windows acceptance is a single OAuth-first profile

`scripts/validate-clean-windows-release.ps1` currently requires `AuthorizationServerIssuer`, `CanonicalResourceUri`, `ValidationResourceId`, and `CODEMCP_RS_VERIFICATION_SECRET` for Cloudflare Prepare/doctor. Its tests assert OAuth mode and external auth-server behavior. This must be split later into:

- **5.5.7A:** recommended ChatGPT No-Auth + Cloudflare Network Trust; requires tunnel token via Windows DPAPI, public URL, exact allowed host, `auth.mode = none`, and `network_trust.mode = cloudflare-chatgpt`; does not require `CODEMCP_RS_VERIFICATION_SECRET`.
- **5.5.7B:** optional External OAuth Resource Server interoperability; retains the existing issuer/resource/contract/secret and RFC 9728 evidence.

The existing 5.5.7B evidence is retained and must not be marked failed or deleted.

#### P2 — Documentation and compatibility language still describe the old primary path

The README, Cloudflare guide, external-auth guide, security/threat model, acceptance material, and parts of the Phase 5.5 plan still describe Cloudflare as transport-only with OAuth as the required public path. The legacy OpenAI provider and its fallback behavior also remain in the code and must not be removed. These are documentation/contract alignment tasks for later phases; Phase A does not rewrite the operator guides.

### A.5 Phase A architecture decisions for implementation

The following decisions are the implementation contract for the next explicit continuation:

1. **Cloudflare is the network enforcement boundary.** The Bridge never evaluates a client IP from HTTP headers and never embeds the current OpenAI ranges. WAF admission precedes Tunnel admission.
2. **Host trust is a second Bridge boundary.** Use the actual `Host` header only, exact case-insensitive hostname comparison, default-port canonicalization for `:443`, no suffix matching, no wildcard v1, and no forwarded-host override.
3. **Origin is auxiliary.** Validate it only when present; absence does not reject a ChatGPT backend request. It is not the authentication boundary.
4. **No Auth means no OAuth installation.** Profile A must not install a bearer authenticator and must allow `/mcp` without `Authorization`, while still applying network trust and all existing project/operation/security policies.
5. **The network principal is synthetic and explicit.** It exists only for audit, operation ownership, session binding, and replay isolation. It never claims to identify an individual ChatGPT user.
6. **OAuth remains intact.** `mcp-rs-verification-v1`, RFC 9728 metadata/challenges, OAuth subjects/clients/scopes, and the external `mcp-auth-server` boundary remain available only through Profile B and must keep their current fail-closed behavior.
7. **Public startup is fail-closed.** Cloudflare public transport plus No Auth starts only when `network_trust.mode = cloudflare-chatgpt` and `allowed_hosts` is non-empty and valid; otherwise it fails with a stable error such as `PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST`. Local loopback-only development is a separate case.
8. **Health is local-only.** Lifecycle health checks remain on loopback; public routing must not expose `/healthz` as an unintended public endpoint.
9. **Backward compatibility is explicit.** The existing OpenAI transport remains available, and existing OAuth runtime state must either load unchanged or fail with a clear migration error; it must not silently become network-trusted.

### A.6 Deferred phase sequence and stop gates

Each item is a separate stop-gated phase. No later phase starts automatically:

| Phase | Scope | Required output |
| --- | --- | --- |
| B | Auth/network-trust config model and fail-closed validation | Explicit profiles, unknown-mode rejection, targeted config tests |
| C | Host/Origin middleware, common auth context, synthetic principal, replay/session isolation | Exact host/origin matrix and OAuth namespace regression tests |
| D | CLI/init, doctor, lifecycle startup gate, health routing | Profile-aware CLI/doctor and public-start fail-closed tests |
| E | Clean Windows 5.5.7A/5.5.7B harness | No-Auth recommended acceptance path plus preserved OAuth path |
| F | Cloudflare deployment runbook and optional IP-list helper decision | WAF boundary documentation; manual provisioning if manifest contract is unstable |
| G | Full security regression and installer impact review | Full test/lint/build/installer review with clean tree |
| H | Live acceptance preparation instructions only | Stopped, evidence-oriented instructions; no live cleanup or v0.1.0 freeze |

### A.7 Risks requiring explicit review later

- The official OpenAI Connector egress manifest URL/schema and update cadence are not part of this repository’s current contract. Do not build a brittle updater until that contract is confirmed.
- Cloudflare WAF/IP List state lives outside the repository; live evidence must distinguish WAF `BLOCK` from Bridge/Tunnel behavior and must not be substituted with a Python header check.
- Adding auth-context or replay namespace state may require a backward-compatible SQLite migration. Existing OAuth records, checkpoints, approvals, and session ownership must remain readable and safe.
- The current public-capable `/healthz` route and externally managed Tunnel ingress need an explicit route-level review before claiming that health is loopback-only.
- Existing documentation and release harness assertions are intentionally inconsistent with Profile A until their later phase updates. This is known migration work, not evidence that OAuth was abandoned.

**Phase A gate:** COMPLETE for read-only review. The branch and review artifact were recorded before Phase B implementation began. The Phase B result is recorded below.

## Phase B — Auth none + network-trust configuration model and validation

**Status:** **COMPLETE — 2026-08-26.**

### B.1 Scope

This phase implemented only the configuration/model/schema/validation foundation for the two security profiles. At B completion, the lifecycle start gate, doctor output, CLI flags, HTTP middleware, synthetic principal, replay/session identity, Windows harness, WAF, IP manifest, and live endpoint work remained deferred; Phase C implementation is recorded below.

The lifecycle-owned, versioned `config/remote.toml` remains the canonical persisted runtime configuration. Authentication and network trust are separate tables and separate models:

```toml
[auth]
version = 1
mode = "none"

[network_trust]
mode = "cloudflare-chatgpt"
allowed_hosts = ["mcp.example.com"]
allowed_origins = ["https://chatgpt.com"]
```

The existing OAuth shape remains supported without a network-trust table:

```toml
[auth]
version = 1
mode = "oauth-resource-server"
verification_contract = "mcp-rs-verification-v1"
authorization_server_issuer = "https://auth.example.com"
canonical_resource_uri = "https://mcp.example.com/mcp"
validation_resource_id = "codemcp-resource"
validation_timeout_ms = 2000
```

### B.2 Implemented contract

- Added independent `NetworkTrustConfig` and `NetworkTrustConfigError` in `bridge/src/codemcp_bridge/network_trust.py`.
- Supported network-trust mode is exactly `cloudflare-chatgpt`; unknown modes fail closed.
- `allowed_hosts` is required and non-empty. Entries are hostname-only, canonicalized to lowercase, and reject schemes, ports (including `:443`), paths, queries, fragments, credentials, wildcard characters, IP literals, invalid labels, whitespace, and control characters.
- `allowed_origins` is optional and may be empty. Entries must be complete HTTPS origins without credentials, path, query, fragment, `null`, or wildcards. Hostnames are canonicalized case-insensitively and default HTTPS port `:443` is removed.
- Unknown `network_trust` fields fail closed. No HTTP request enforcement is introduced here; Origin remains an if-present policy for the later middleware phase.
- Supported auth modes are exactly `none` and `oauth-resource-server`. Unknown auth modes fail closed.
- Explicit `auth.mode = "none"` requires a valid `network_trust` table with `mode = "cloudflare-chatgpt"` and non-empty `allowed_hosts`. Missing or invalid trust policy fails closed during combined configuration loading.
- `auth.mode = "oauth-resource-server"` continues to validate the existing issuer/resource/contract fields and does not require `network_trust`.
- `configure_network_trust()` persists canonical values while preserving an existing OAuth configuration. `configure_resource_auth(mode="none")` persists an explicit No-Auth mode only when a valid network-trust policy already exists.
- No `anonymous` principal, OAuth principal change, replay namespace change, session owner change, audit principal change, HTTP middleware, or public-start enforcement was added in Phase B.

### B.3 Backward compatibility

Legacy configurations with no `[auth]` table retain the existing disabled/legacy parsing result. Existing OAuth configurations without `[network_trust]` continue to load successfully and retain `mcp-rs-verification-v1` validation semantics. Existing OAuth secret handling and RFC 9728 behavior were not changed.

An explicit No-Auth configuration is intentionally different from an absent legacy auth table: it must include the network-trust policy and cannot receive a permissive default. `remote.toml` serialization writes canonical host/origin arrays and an explicit `auth.mode = "none"` for the configured Profile A state.

### B.4 Validation evidence

Changed tests cover:

- OAuth configuration with no network trust;
- valid No-Auth plus Cloudflare network trust;
- missing/empty trust policy and unknown auth/network modes;
- hostname-only validation, lowercase canonicalization, wildcard/scheme/path/port/credential/query/fragment rejection;
- valid HTTPS origins, `:443` canonicalization, empty origin lists, and rejection of HTTP/path/query/credentials/`null`/wildcards/malformed values;
- OAuth preservation when adding network trust;
- explicit No-Auth configuration refusal without trust;
- TOML round-trip and canonical serialization.

Validation on 2026-08-26:

- targeted Phase B plus OAuth, Cloudflare, security-gate, and Phase 3 lifecycle regression: `97 passed`;
- Ruff check on changed Python files: PASS;
- Ruff format check on changed Python files: PASS;
- Python `compileall`: PASS;
- `git diff --check`: PASS.

**STOP GATE:** Phase B is COMPLETE. Phase C is recorded below; Phase D still requires a new explicit `继续` instruction.

## Phase C — Host/Origin enforcement, network principal, and replay/session isolation

**Status:** **COMPLETE — 2026-08-26.**

### C.1 Scope

Phase C adds the request-time boundaries and common security context required by the explicit `network-trusted` profile. It does not add CLI/init flags, doctor output, public-start lifecycle enforcement, Windows acceptance, Cloudflare WAF/API integration, IP-manifest synchronization, or live endpoint behavior.

The network-trust middleware is an outer ASGI boundary on the Bridge application. It therefore protects `/mcp`, `/healthz`, metadata, and future HTTP routes before route dispatch. It uses the actual request `Host` or protocol authority, with exact case-insensitive hostname matching against the validated `allowed_hosts` configuration. An absent port and literal `:443` canonicalize to the same hostname; other ports, malformed authorities, duplicate/conflicting Host or authority values, subdomains, suffix attacks, and forwarded-host overrides are rejected. `X-Forwarded-Host`, `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, and `Cf-Access-*` are not authorization inputs.

Origin is an auxiliary if-present check. A missing Origin is accepted; a present value must exactly match the canonical `allowed_origins` HTTPS list. Default `:443` is canonicalized, while HTTP, subdomain, suffix-attack, path/query/credential, `null`, malformed, or duplicate values are rejected. Origin is not an authentication boundary and does not identify a caller.

Rejected requests receive a fixed JSON `403` response with `Cache-Control: no-store`; raw Host/Origin values are not reflected in the response or denial log. A valid request receives a deterministic `NetworkTrustedPrincipal` through the existing auth-context scope. Its audit projection is explicitly network-only:

```text
auth_kind = network-trusted
auth_type = network-trusted
trust_profile = cloudflare-chatgpt
principal = network-chatgpt-v1
issuer = network-trust://cloudflare-chatgpt
subject = network-chatgpt-v1
identity_level = network-only
replay_namespace = network-chatgpt-v1
```

This synthetic principal is only an internal deployment/network identity for audit, operation ownership, session binding, and replay partitioning. It is not a ChatGPT user, Workspace, account, conversation, or WAF attestation. The OpenAI/ChatGPT egress IP allowlist remains an external Cloudflare Edge/WAF network trust boundary; no client IP is evaluated in Python.

### C.2 OAuth compatibility and isolation

The existing `mcp-rs-verification-v1` OAuth Resource Server path remains unchanged as a separately installable profile. Its `AuthenticatedPrincipal`, Bearer challenge/validation behavior, OAuth subject/client/scope fields, RFC 9728 metadata, and external `mcp-auth-server` boundary remain available. Network trust and OAuth installation are mutually exclusive on one Bridge server, and a network-profile request's `Authorization` header is not routed to an OAuth verifier.

The common auth context accepts either the existing OAuth principal or the new network principal. Network operations use the fixed `network-chatgpt-v1` replay namespace. OAuth operations use `oauth-<stable-digest>` derived from the frozen OAuth identity tuple `(contract_version, issuer, resource, subject, client_id)`. The namespace is stored in the existing text `client_request_id` column with an internal control-character separator, while `OperationRecord.client_request_id` continues to expose the caller's original ID. Thus OAuth and network-trusted calls cannot share a replay namespace or idempotency record. Existing raw OAuth replay keys remain readable through a conservative compatibility lookup; they are never reinterpreted as network-trusted records. No database schema migration is required.

Session auth context is recorded as an `auth.session.context` audit event in the same transaction as session creation. Active-session checks and reconcile successor checks require the current auth context to match the persisted context. The existing `local-policy` owner remains the storage/daemon owner and is not used as caller identity. Malformed persisted auth-context JSON fails closed.

### C.3 Integration surface and validation

The Bridge server factory accepts the optional `network_trust` configuration and canonical resource, and `install_network_trust()` supports installation before ASGI app creation. The default server factory and OAuth installation remain backward compatible. Phase C tests cover the exact Host/authority matrix, forwarded-header rejection, Origin if-present semantics, fixed denial responses, `/healthz` protection, no-Auth MCP access, deterministic principal/audit fields, OAuth/network replay isolation, legacy OAuth replay readability, session isolation, and profile-installation exclusion.

Validation on 2026-08-26:

- Phase C runtime matrix: `45 passed`;
- existing OAuth Resource Server regression: `30 passed`;
- Phase B configuration plus OAuth, Cloudflare, security-gate, Phase 3 persistence/lifecycle regression: `150 passed`;
- related Phase 2 MCP server integration regression: `19 passed`;
- Python `compileall`, Ruff format check, Ruff check, and `git diff --check`: PASS.

**STOP GATE:** Phase C is COMPLETE. Phase D (CLI/init, doctor, public-start fail-closed enforcement, and health lifecycle routing) has not started and requires a new explicit `继续` instruction.

## 1. Context

Packaging Phase 5 already proved most of the Windows distribution path:

- native Windows worker works without WSL2;
- the PyInstaller executable works without Python/uv/pwsh on the isolated runtime PATH;
- the Inno Setup installer works as a per-user install;
- DPAPI-backed secret persistence works across processes;
- Bridge and tunnel lifecycle health checks work;
- remote `project_open`, `file_read`, Git mutation, Bridge-owned checkpoint creation, and idempotent replay were exercised against the disposable clean-machine project.

The remaining Phase 5 acceptance work exposed two things:

1. the clean-machine disposable project template needs a deterministic profile marker so `development_ready=true` can be validated;
2. the current OpenAI Secure MCP Tunnel path is not the best long-term default for a self-hosted/open-source distribution.

A ChatGPT custom MCP/plugin flow was also verified to support a public HTTP MCP URL with OAuth configuration, including client metadata/DCR-related settings, scopes, authorization URL, token URL, and registration URL.

The authentication architecture is now deliberately separated from the transport architecture. Cloudflare is responsible only for publishing the loopback MCP endpoint over HTTPS. OAuth authorization-server responsibilities belong to an independent, reusable `mcp-auth-server` project rather than to Cloudflare Access or to `codemcp-remote`.

Phase 5.5 therefore inserts a transport abstraction plus generic OAuth resource-server integration before the final clean-machine freeze.

## 2. Goal

Make remote transport replaceable, add a production-quality Cloudflare path, and integrate `codemcp-remote` with an external standards-based MCP authorization server:

```text
ChatGPT
  |
  | OAuth 2.0 / PKCE / DCR as negotiated
  v
Independent mcp-auth-server
  |
  | access token
  v
HTTPS MCP endpoint
  |
  v
Cloudflare Tunnel
  |
  v
127.0.0.1:46200/mcp
  |
  | generic OAuth resource-server validation
  v
codemcp-remote Bridge
  |
  v
native codemcp worker + Git/checkpoint policy
```

Cloudflare becomes the recommended/default remote transport for self-hosted users.

The independent `mcp-auth-server` owns OAuth protocol state and user/client authorization. `codemcp-remote` acts only as an OAuth-protected MCP resource server and keeps its existing project/tool/Git policy as the authoritative operation-authorization layer unless a later phase explicitly adds verified scope mapping.

The existing OpenAI Secure MCP Tunnel implementation remains available as an optional compatibility provider until a later explicit removal decision.

## 3. Non-goals

Phase 5.5 must not:

- redesign MCP tools;
- redesign Git/checkpoint semantics;
- weaken mutation approval, CAS rollback, idempotency, project isolation, or audit behavior;
- expose the Bridge on `0.0.0.0`;
- implement login UI, DCR persistence, authorization-code issuance, refresh-token issuance, consent storage, user management, or an OAuth authorization server inside `codemcp-remote`;
- couple `codemcp-remote` to the internal database, runtime, or source code of `mcp-auth-server`;
- make arbitrary shell execution available;
- require Python, uv, pwsh, or WSL2 in the packaged runtime;
- remove the existing OpenAI Tunnel provider in the same phase;
- begin Phase 6 automatically.

## 4. Architectural decisions

### 4.1 Transport abstraction

Introduce one provider boundary for lifecycle operations.

Conceptual contract:

```text
RemoteTransportProvider
- initialize(...)
- validate_config(...)
- start(...)
- status(...)
- stop(...)
- doctor(...)
- redact(...)
```

Initial implementations:

```text
cloudflare
openai-tunnel
```

Bridge/MCP/Git/checkpoint code must not know which remote transport is active.

### 4.2 Loopback-only Bridge remains mandatory

The Bridge continues to listen only on:

```text
127.0.0.1:46200
```

The remote provider establishes the outbound/public path.

No firewall port opening and no direct public Bridge bind are allowed.

### 4.3 Authentication model

Preferred authentication chain:

```text
ChatGPT
  -> independent mcp-auth-server
  -> Bearer access token
  -> Cloudflare Tunnel
  -> codemcp-remote Bridge resource-server validation
```

`mcp-auth-server` is the OAuth authorization layer and uses `@cloudflare/workers-oauth-provider` as its generic OAuth 2.1 protocol engine. The auth-server project owns MCP-specific Resource Registry / issuer / scope / audience policy, while the Provider owns the standards OAuth state machine such as Authorization Code + PKCE, client/grant lifecycle, token issuance, refresh and revoke.

`codemcp-remote` is an OAuth Resource Server only. It accepts MCP requests after validating the externally issued access token according to the **versioned Resource Server Verification Contract** frozen by `mcp-auth-server` Phase 4. Cloudflare Tunnel remains transport only and is not an identity authority.

Minimum Bridge semantics, independent of token representation:

- extract the standard Bearer token from the HTTP Authorization header;
- validate token authenticity/active state using only the frozen Resource Server Verification Contract;
- require the configured authorization-server issuer/binding;
- require exact canonical MCP resource/audience matching;
- enforce expiry/revocation semantics defined by the contract;
- extract only documented caller/client identity fields;
- consume only documented scopes and never infer authorization from token presence;
- fail closed on missing, malformed, expired, revoked, unverifiable, wrong-resource, or wrong-issuer credentials;
- never trust Cloudflare-specific identity headers as an authorization source.

Resource Server Verification Contract v1 is now frozen as **Provider-native opaque bearer + authenticated online validation**. Phase 5.5.3 therefore implements `POST {issuer}/mcp/resource-server/validate` with per-Resource-Server HTTP Basic authentication, a two-second total timeout, no automatic retry, zero positive/negative cross-request cache, exact issuer/resource checks, local expiry recheck, and fail-closed `401`/`503` mapping. JWT/JWKS is not part of v1.

`mcp-auth-server` Phase 4.0 is VERIFIED and Phase 4.1 is FROZEN. Phase 5.5.3 consumes only `mcp-rs-verification-v1` and does not parse Provider-private token structure or access Provider storage directly.

### 4.4 Scope handling

OAuth scope enforcement is not assumed before interoperability evidence.

Phase 5.5.0 must determine whether ChatGPT and the independent `mcp-auth-server` can negotiate stable scopes suitable for MCP authorization.

If verified, a minimal mapping may be designed, for example:

```text
codemcp:read
codemcp:write
codemcp:execute
codemcp:checkpoint
```

If scope semantics are not proven end-to-end, OAuth is used for authentication/identity only and the existing Bridge policy remains the authoritative tool authorization layer.

Do not invent unsupported scope semantics or infer authorization from token presence alone.

### 4.5 Cross-project boundary

`mcp-auth-server` is a separate project and release lifecycle.

The integration contract must be protocol-based, not source-based:

```text
codemcp-remote
  -> authorization-server issuer / metadata
  -> canonical MCP resource identifier
  -> versioned Resource Server Verification Contract
       -> JWT/JWKS verification, OR
       -> authenticated online validation/introspection
  -> documented identity/scopes/error semantics
```

`codemcp-remote` must not:

- import auth-server or Provider packages;
- read the auth-server D1/KV/provider storage directly;
- depend on auth-server filesystem/runtime state;
- share Provider storage secrets or private signing keys;
- require the auth server to run on the same machine;
- proxy login/consent pages.

The token representation is not selected in this repository. `codemcp-remote` consumes the contract frozen by `mcp-auth-server` Phase 4 and implements only the selected Resource Server verification mode.

Cross-project execution gate:

```text
mcp-auth-server Phase 3  Cloudflare OAuth Provider integration       COMPLETE
codemcp-remote 5.5.1     transport abstraction                      COMPLETE
codemcp-remote 5.5.2     Cloudflare transport provider              COMPLETE
codemcp-remote 5.5.4A    transport CLI/config                       COMPLETE

mcp-auth-server Phase 4.0  characterize Provider token behavior
mcp-auth-server Phase 4.1  freeze Resource Server Verification Contract v1
                              |
                              +--> unlock codemcp-remote 5.5.3
                              +--> unlock codemcp-remote 5.5.4B

mcp-auth-server Phase 5/6 and codemcp-remote 5.5.3/5.5.4B may then proceed in parallel.
mcp-auth-server Phase 7 is the hardened integration guide/reference layer, not the first contract-definition gate.
```

Phase 5.5.3 must not begin from assumptions about `@cloudflare/workers-oauth-provider` token internals. It begins only after the auth-server Phase 4.1 handoff is versioned and testable.

### 4.6 Cloudflare tunnel credential

The Cloudflare tunnel credential/token is a local machine secret.

Requirements:

- never commit it;
- never write it to plaintext TOML/env files;
- never print it in logs or JSON status output;
- store it using the existing Windows DPAPI secret mechanism or a generalized DPAPI secret store;
- child `cloudflared.exe` receives it only through the process environment or another supported ephemeral mechanism.

### 4.7 Packaged cloudflared

The Windows installer may bundle a pinned `cloudflared.exe`.

Release provenance must record:

- exact version;
- upstream download URL;
- upstream license;
- SHA-256;
- local packaged SHA-256;
- source/provenance record.

The build must fail closed on checksum mismatch.

## 5. Phase breakdown

Each sub-phase is independently gated.

**Complete one sub-phase, report evidence, then stop and wait for explicit authorization before starting the next one.**

---

## Phase 5.5.0 — External MCP Auth + HTTP MCP compatibility spike

**Status: PARTIAL — preliminary protocol evidence recorded; final token-verification/interoperability acceptance waits on `mcp-auth-server` Phase 4.1.**

### Objective

Prove the standards contract among ChatGPT, the independent `mcp-auth-server`, Cloudflare transport, and the MCP Resource Server before starting auth-dependent production integration.

This phase is an interoperability gate, not an implementation phase for either project.

### Preconditions

A disposable/testable `mcp-auth-server` deployment must exist with enough functionality to exercise the intended ChatGPT flow. Its internal implementation is out of scope for this repository.

### Work

Create a disposable test path and verify:

1. ChatGPT can connect to an HTTPS `/mcp` endpoint through Cloudflare Tunnel.
2. Streamable HTTP MCP tool discovery works.
3. The MCP resource server can advertise or trigger the OAuth discovery flow ChatGPT expects.
4. ChatGPT can discover the independent auth server through the final metadata contract.
5. CIMD/client metadata behavior is recorded.
6. Dynamic Client Registration behavior is recorded when used.
7. Exact redirect URI registration/matching behavior is recorded.
8. Authorization Code + PKCE S256 compatibility is proven when that flow is used.
9. Token issuance succeeds.
10. Refresh-token/session renewal behavior is recorded when supported.
11. Exact access-token presentation to the MCP resource server is captured with secrets redacted.
12. Token format is classified as signed/self-contained or opaque.
13. For signed tokens, issuer, audience/resource, JWKS, subject/identity and expiry claims are recorded.
14. For opaque tokens, the required introspection contract is recorded before any Bridge implementation.
15. 401 vs 403 behavior for unauthenticated/unauthorized calls is recorded.
16. A read tool call succeeds through the authenticated path.
17. A write tool call succeeds through the authenticated path.
18. Reconnect after token refresh/session expiration is exercised.
19. Whether meaningful custom OAuth scopes can be negotiated and enforced end-to-end is recorded.
20. Cloudflare is confirmed to act only as HTTPS transport; no Cloudflare identity header is required for authorization.

### Deliverables

- `docs/reports/compatibility/mcp-auth-server-chatgpt-spike.md`;
- a redacted standards contract covering discovery, DCR/CIMD, redirect URIs, PKCE, token presentation, issuer/audience/resource, JWKS or introspection, refresh behavior and error semantics;
- explicit PASS/FAIL matrix;
- explicit list of assumptions that remain unproven;
- reference to the independent auth-server build/version/commit used for the spike when available.

The existing `docs/reports/compatibility/cloudflare-chatgpt-oauth-spike.md` remains historical evidence from the pre-pivot Cloudflare-Access design and must not be treated as the final auth contract.

### Stop conditions

Stop Phase 5.5 before production refactoring if any of these cannot be proven:

- ChatGPT cannot use the Cloudflare-published Streamable HTTP MCP endpoint;
- OAuth cannot complete reliably against the independent auth server;
- the MCP resource server cannot cryptographically validate or safely introspect the issued access token;
- issuer/audience/resource semantics cannot prevent token reuse against the wrong MCP resource;
- write-tool calls cannot work through the selected ChatGPT OAuth flow.

Do not fall back to a shared static bearer secret merely to pass the spike.

Transport-only work that does not depend on the auth contract (`5.5.1`, `5.5.2`, `5.5.4A`) is explicitly allowed to proceed in parallel and is already complete. **No auth production integration (`5.5.3` or `5.5.4B`) starts until `mcp-auth-server` Phase 4.1 freezes a testable Resource Server Verification Contract and the relevant Phase 5.5.0 interoperability checks pass.**

---

## Phase 5.5.1 — Transport provider abstraction

**Status: COMPLETE — 2026-08-25.**

### Objective

Refactor the current tunnel lifecycle behind a provider interface without behavior change.

### Work

Expected code areas:

- `bridge/src/codemcp_bridge/lifecycle.py`
- new `bridge/src/codemcp_bridge/transports/`
- `bridge/src/codemcp_bridge/main.py`
- lifecycle tests.

Suggested layout:

```text
transports/
  __init__.py
  base.py
  openai_tunnel.py
```

Move OpenAI-specific responsibilities out of generic lifecycle code:

- tunnel profile validation;
- OpenAI control-plane environment;
- `tunnel-client` discovery;
- tunnel-client process startup;
- provider health;
- provider log redaction.

Generic lifecycle retains:

- process ownership;
- PID + process creation-time validation;
- start/status/stop orchestration;
- atomic state files;
- log rotation helpers;
- Bridge lifecycle.

### Compatibility requirement

Existing OpenAI Tunnel behavior must remain regression-compatible at the end of this phase.

### Validation

- existing lifecycle tests pass;
- existing OpenAI Tunnel profile tests pass;
- packaged source-mode behavior unchanged;
- no Cloudflare implementation yet.

### Completion criteria

The Bridge can select an `openai-tunnel` provider through the new abstraction with no externally observable regression.

Then STOP.

---

## Phase 5.5.2 — Cloudflare transport provider

**Status: COMPLETE — 2026-08-25.**

### Objective

Add `cloudflare` as a second remote transport.

### Configuration model

Target conceptual configuration:

```toml
[remote]
transport = "cloudflare"

[remote.cloudflare]
public_url = "https://mcp.example.com/mcp"
origin_url = "http://127.0.0.1:46200/mcp"
```

Tunnel credential is not stored in this TOML.

### Work

Implement:

- `cloudflared.exe` discovery;
- pinned bundled binary support;
- DPAPI-backed tunnel token storage;
- cloudflared process startup;
- provider status;
- provider health;
- process ownership protection;
- safe stop;
- redacted logs;
- startup timeout;
- fail-closed config validation.

### Security constraints

Reject:

- non-HTTPS public MCP URLs;
- origin host other than loopback;
- origin path other than configured MCP path;
- plaintext tunnel token in config;
- arbitrary cloudflared argv injection;
- user-provided executable paths outside approved discovery rules unless explicitly designed and tested.

### Validation

- unit tests for config parsing;
- token redaction tests;
- stale PID/PID reuse tests;
- cloudflared missing/bad-version behavior;
- mocked provider health;
- real local smoke when available.

Then STOP.

---

## Phase 5.5.3 — MCP OAuth Resource Server verification integration

**Status: COMPLETE — 2026-08-25, implemented against frozen `mcp-rs-verification-v1`; live cross-project acceptance remains in the later interoperability/security gates.**

### Objective

Make credentials issued by the independent `mcp-auth-server` enforceable at the Bridge without coupling `codemcp-remote` to Cloudflare OAuth Provider internals, D1/KV storage, or auth-server source code.

### Input contract

Phase 5.5.3 consumes exactly one versioned handoff from `mcp-auth-server` Phase 4.1. The handoff must define at least:

- canonical authorization-server issuer;
- canonical MCP resource identifier and exact audience semantics;
- token representation;
- verification mode: local signed-token verification or authenticated online validation/introspection;
- token lifetime and revocation semantics;
- Resource Server authentication requirements for online verification, if any;
- stable identity fields;
- stable scope representation;
- 401/403 behavior;
- cache and failure semantics;
- test vectors for wrong-resource and revoked/invalid credentials.

Do not implement from Provider internals that are not part of this frozen contract.

### Configuration model

The frozen v1 consumer requires these Resource Server-side values:

```text
authorization_server_issuer
canonical_resource_uri
validation_endpoint = {issuer}/mcp/resource-server/validate
validation_resource_id
validation_secret
validation_timeout_ms = 2000
verification_contract_version = 1
```

Phase 5.5.3 implements the verification objects and transport installation boundary. Persistent TOML/DPAPI wiring for these values belongs to Phase 5.5.4B and is intentionally not started here. `validation_secret` is a dedicated Resource Server secret and must never be written to ordinary plaintext configuration or logs.

### Common implementation

Regardless of representation:

- extract the standard Bearer token;
- reject duplicate/conflicting credentials;
- validate issuer/resource/audience exactly;
- enforce active/expiry/revocation semantics;
- propagate documented identity into request/audit context;
- extract only documented scopes;
- keep Cloudflare identity headers non-authoritative;
- fail closed when verification cannot establish an active credential for this exact MCP resource.

### Signed-token path

**Not selected for Resource Server Verification Contract v1.** No JWT parser, signature verifier, JWKS dependency, or key cache is added in Phase 5.5.3.

### Provider-native opaque-token path — selected v1 implementation

Implement and preserve the frozen online validation contract:

- `POST {issuer}/mcp/resource-server/validate`;
- authenticate `codemcp-remote` with HTTP Basic `resource_id:verification_secret`;
- send only `{token, resource}` as the v1 JSON body;
- use a two-second **total** validation budget and no automatic retry;
- follow no redirects;
- require `200`, JSON, `Cache-Control: no-store`, `contract_version == "1"` and boolean `active`;
- use zero positive and zero negative cross-request validation cache;
- map `active: false` to MCP-client `401`;
- recheck exact issuer, exact canonical resource and `expires_at > now` locally for `active: true`;
- map validation service/protocol/auth failures to MCP-client `503` fail closed;
- never send the MCP bearer token anywhere except the designated auth-server verification endpoint;
- redact bearer credentials, Basic credentials and `verification_secret` from logs/errors/audit.

### Health endpoint rule

Local lifecycle health checks must keep working without making `/healthz` a public privileged path.

The public tunnel exposes only the MCP/auth-discovery surface required by the final contract; local lifecycle diagnostics remain loopback-safe.

### Negative tests

Must include representation-independent cases:

- no Authorization header;
- malformed/duplicate Bearer header;
- malformed/invalid credential;
- expired credential;
- revoked/inactive credential;
- wrong issuer;
- wrong canonical audience/resource;
- token/credential issued for a different MCP resource;
- spoofed Cloudflare identity headers having no authorization effect;
- verification dependency unavailable -> fail closed.

Additionally:

- signed mode: forged signature, unknown/stale key, invalid time claims;
- opaque mode: invalid introspection/validation response, Resource Server authentication failure, timeout, revoked token and cache-expiry behavior.

### Scope gate

If the frozen contract proves stable scopes, add explicit scope-to-operation enforcement with tests for read/write/execute/checkpoint boundaries.

If scope semantics are not yet frozen, OAuth establishes caller identity/resource validity while existing Bridge policy remains authoritative for operation authorization. Do not invent scope meanings locally.

### Phase 5.5.3 completion evidence

Implemented:

- `bridge/src/codemcp_bridge/resource_auth.py`;
- MCP transport pre-dispatch authentication hook with one-time public authenticator installation;
- authenticated principal propagation into operation execution context;
- safe `auth.context` audit projection without bearer or verification secrets;
- idempotency replay binding to stable `(contract_version, issuer, resource, subject, client_id)` identity;
- frozen-contract tests in `bridge/tests/test_phase55_oauth_resource_server.py`.

Validation on 2026-08-25:

- all Phase 5.5.3 tests passed in the full registered test run;
- full suite result: `189 passed, 4 skipped, 4 failed`;
- the four remaining failures are inherited pre-existing Windows/symlink/line-ending/Maven baseline failures and are not introduced by Phase 5.5.3;
- a registered format invocation was blocked by the connector safety layer, so no format PASS is claimed in this phase;
- live validation against an implemented `mcp-auth-server` endpoint remains a later cross-project interoperability/security acceptance item because the auth-server Phase 4.2 implementation/acceptance is not part of this sub-phase.

Then STOP.

---

## Phase 5.5.4 — CLI, configuration, migration, and doctor

### Objective

Make transport selection and external OAuth resource-server validation supported product configuration rather than code-path switches.

### Parallel execution split

Phase 5.5.4 remains split into two independently gated parts:

- **Phase 5.5.4A — transport-only CLI/config**: COMPLETE — versioned transport selection, backward-compatible OpenAI fallback/migration behavior, Cloudflare CLI parameters, provider-specific DPAPI transport secrets, provider-aware start/status/stop/doctor.
- **Phase 5.5.4B — auth-aware configuration**: **COMPLETE** — persistent configuration/DPAPI wiring for `mcp-rs-verification-v1`, auth-aware doctor/status, startup wiring, and structural validation.

`mcp-auth-server` Phase 4.1 is FROZEN, so the previous dependency block is removed. Phase 5.5.4B completed as a separate stop-gated sub-phase and did not enter Phase 5.5.5.

Phase 5.5.4A and 5.5.4B both completed on 2026-08-25. Phase 5.5.4 is now fully complete.

### CLI target

Conceptually:

```text
codemcp-remote init --transport cloudflare
codemcp-remote start
codemcp-remote status
codemcp-remote stop
codemcp-remote doctor
```

OpenAI compatibility remains selectable:

```text
codemcp-remote init --transport openai-tunnel
```

### Work

- versioned transport-provider configuration;
- versioned generic OAuth resource-server configuration;
- backward-compatible migration for current OpenAI runtime state;
- generalized local secret storage for transport credentials and Resource Server verification credentials that actually require secrecy;
- public auth metadata configuration for exact issuer, canonical resource, validation Resource Server id, frozen verification contract and fixed validation timeout;
- provider-aware and auth-aware `doctor`;
- provider-aware and auth-aware JSON status;
- provider-aware startup errors;
- provider-specific transport validation;
- auth-specific structural validation;
- no provider or Resource Server verification secret leakage;
- no auth-server private signing material or user/client database copied into the local runtime.

### Doctor requirements for Cloudflare + external auth

At minimum report:

- transport = cloudflare;
- Bridge loopback configuration valid;
- cloudflared binary found;
- tunnel credential available from DPAPI;
- public MCP URL structurally valid;
- auth mode = oauth-resource-server;
- exact issuer structurally valid;
- canonical resource identifier structurally valid;
- `mcp-rs-verification-v1` contract selection, validation endpoint, Resource Server id and fixed 2-second validation timeout structurally valid;
- Resource Server verification secret availability/source without exposing the secret;
- no auth-server private signing material is present locally;
- Git prerequisite available;
- native worker mode = local.

Do not make successful public internet access, live auth-server reachability, or successful token issuance a prerequisite for an offline configuration check unless the command explicitly performs a network diagnostic.

### Phase 5.5.4B completion evidence

Implemented on 2026-08-25:

- versioned `[auth]` configuration in `remote.toml` for exact issuer, canonical resource, validation Resource Server id, `mcp-rs-verification-v1`, and fixed 2000 ms timeout;
- strict auth-config parsing that rejects unknown/plaintext secret fields and structurally validates the frozen Resource Server contract;
- dedicated `CODEMCP_RS_VERIFICATION_SECRET` runtime input with Windows DPAPI persistence in `mcp-rs-verification-secret.dpapi`;
- auth-aware `status` and offline `doctor` output that reports only public configuration and secret availability/source;
- Cloudflare startup fail-closed when Resource Server auth configuration or its verification secret is missing;
- managed `serve` propagation of the lifecycle app root and installation of the configured request authenticator through the frozen `install_request_authenticator()` boundary;
- explicit CLI wiring for auth mode, issuer, canonical resource, validation Resource Server id, and DPAPI secret storage;
- regression coverage in `bridge/tests/test_phase55_auth_configuration.py`.

Validation on 2026-08-25:

- all seven Phase 5.5.4B tests passed;
- full registered suite result: `196 passed, 4 skipped, 4 failed`;
- the four remaining failures are the inherited baseline: Windows symlink privilege, Maven acceptance, and two Windows CRLF/SHA-256 expectations;
- the Windows onedir executable build/smoke returned to PASS after formatting the files changed by 5.5.4B;
- the repository-wide registered `format` command still reports pre-existing formatting debt in seven files outside this sub-phase's change scope, so no repository-wide format PASS is claimed;
- no live auth-server reachability or token issuance was required for this offline configuration phase.

Then STOP.

---

## Phase 5.5.5 — Windows installer integration

### Objective

Ship Cloudflare transport and generic OAuth resource-server support without bundling the independent auth server or reintroducing local runtime dependencies.

### Work

Update:

- `scripts/prepare-tunnel-client.ps1` or replace with provider-neutral preparation scripts;
- Inno Setup payload;
- installer build script;
- release manifest;
- SHA256SUMS;
- license/provenance notices.

Recommended rename:

```text
prepare-remote-transport.ps1
```

or provider-specific:

```text
prepare-cloudflared.ps1
prepare-openai-tunnel-client.ps1
```

### Installer contract

The installed Windows payload still requires locally only:

- Windows 11 x64;
- Git for Windows;
- user-owned Cloudflare account/domain/tunnel configuration for the Cloudflare path.

To use the authenticated ChatGPT path, the user must additionally have access to a compatible external `mcp-auth-server` deployment implementing the versioned Resource Server Verification Contract frozen in auth-server Phase 4.1. That auth server is a network dependency, not a bundled local runtime dependency.

Installed release must still not require or bundle:

- Python;
- uv;
- pwsh;
- WSL2;
- source checkout;
- `mcp-auth-server` source/runtime;
- auth-server private signing keys;
- auth-server user/client/grant databases.

### Validation

- build from clean tree;
- exact cloudflared checksum;
- version smoke;
- installer install/upgrade/uninstall smoke;
- no secret in installer;
- no user runtime data deletion on normal uninstall.

### Phase 5.5.5 implementation and validation state

Implemented on 2026-08-25:

- added `scripts/prepare-cloudflared.ps1` with a hard pin for official `cloudflared` Windows amd64 `2026.7.3`, exact SHA-256 verification before staging, version smoke, and Apache-2.0 license/provenance files;
- corrected the runtime bundled-cloudflared SHA-256 pin to the official `2026.7.3` Windows amd64 digest `8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841`;
- added `scripts/prepare-remote-transport.ps1` so the Windows payload stages Cloudflare as the recommended provider while retaining the OpenAI tunnel client as an optional compatibility provider;
- updated `scripts/build-windows-installer.ps1` to package both transport binaries, reject runtime `remote.toml`/`tunnel.env`, reject every staged `*.dpapi` secret, verify the installed cloudflared version/hash, and define install -> upgrade -> uninstall runtime-data-preservation smoke;
- updated the release-candidate manifest to declare Cloudflare as recommended, record bundled transport provenance, and keep `mcp-auth-server` an external network dependency rather than a bundled runtime;
- added five persistent packaging-contract tests in `bridge/tests/test_phase55_windows_installer.py`.

Validation on 2026-08-25:

- full registered regression before native installer acceptance: `201 passed, 4 skipped, 4 failed`; all five new Phase 5.5.5 packaging-contract tests passed and the same four inherited Windows/symlink/Maven/CRLF failures remained;
- native acceptance using `D:\Programs\Inno Setup 7\ISCC.exe` passed the scoped Ruff gate, all 11 Phase 3 lifecycle tests, PyInstaller onedir build, frozen version/check smoke, frozen worker mutation smoke, Inno Setup compilation, install, upgrade, uninstall, and runtime-data-preservation checks;
- the full registered suite including the one-time native installer acceptance finished at `202 passed, 4 skipped, 4 failed`; the acceptance test passed and the same four inherited baseline failures remained;
- the generated installer is `.local\installer-dist\codemcp-remote-setup.exe` with SHA-256 `0b49d303dcf1994853866672fe2d7623977429dc86e61a52f29ed797b132093b`;
- installer payload checks confirmed the pinned `cloudflared 2026.7.3` checksum/version, no staged runtime auth/tunnel configuration or `*.dpapi` secret, and preservation of user runtime data across upgrade and normal uninstall;
- the temporary acceptance harness was removed from durable history; its partial cleanup operation was reconciled after the repository was manually restored to the pre-operation clean HEAD.

**STOP GATE:** Phase 5.5.5 is COMPLETE. Phase 5.5.6 remains NOT STARTED and must not start without a new explicit continuation instruction.

Then STOP.

---

## Phase 5.5.6 — Security and regression gate

### Objective

Prove the transport refactor did not weaken core safety properties.

### Required regression groups

#### Core unchanged

- project path isolation;
- sensitive-path filtering;
- mutation lock;
- clean-worktree preconditions;
- branch policy;
- checkpoint creation;
- checkpoint finalization CAS;
- idempotency;
- operation audit;
- approval tokens;
- rollback CAS;
- unknown-side-effect handling;
- PID reuse protection.

#### Cloudflare transport-specific

- tunnel-token storage/redaction;
- origin remains loopback;
- no arbitrary cloudflared args;
- public URL validation;
- provider process ownership;
- provider health;
- tunnel restart behavior;
- no Cloudflare identity header is trusted for authorization.

#### Generic OAuth Resource Server-specific

- Bearer token extraction;
- verification mode exactly matches `mcp-auth-server` Resource Server Verification Contract v1;
- issuer validation;
- exact canonical audience/resource validation;
- expiry/revocation/active-state validation defined by the contract;
- auth fail closed;
- wrong-resource credential rejection;
- JWKS or online-validation dependency outage behavior defined by the contract;
- identity propagation into request/audit context;
- scope enforcement only when frozen and proven end-to-end;
- no dependency on auth-server Provider packages, private keys, D1/KV, filesystem or source code.

#### OpenAI provider compatibility

Existing OpenAI provider tests continue to pass unless an explicit deprecation decision is made later.

### Quality gate

- targeted tests pass;
- full regression passes;
- `compileall` passes;
- `git diff --check` passes;
- packaging smoke passes;
- working tree clean.

### Completion evidence

Phase 5.5.6 is **COMPLETE**.

- Core safety regressions remain covered for path isolation, sensitive-path filtering, mutation locking, clean-worktree and branch policy, checkpoint/CAS behavior, idempotency, audit, approvals, rollback, unknown side effects, and PID reuse protection.
- Cloudflare-specific coverage proves DPAPI-backed token handling/redaction, loopback-only origin, fixed cloudflared argv, public URL validation, provider ownership/health, degraded-state restart behavior, and rejection of Cloudflare identity headers as an authorization substitute.
- OAuth Resource Server coverage proves exact `mcp-rs-verification-v1` wire behavior, Bearer extraction, issuer/resource binding, active/expiry handling, wrong-resource rejection, fail-closed validation-service outages, safe identity propagation, and no dependency on auth-server Provider packages or storage/signing internals.
- OpenAI Tunnel compatibility remains covered and passing.
- A Windows command-runner compatibility gap found by the regression gate was fixed without introducing shell execution: fixed registered executables are resolved through Windows `PATH`/`PATHEXT` before `subprocess` execution.
- Historical Windows-only test-environment failures were removed without weakening production policy: symlink tests skip only when the OS account cannot create symlinks, and Git/hash fixtures use stable raw LF bytes.
- Full regression on the final pre-documentation code state: `211 passed, 5 skipped, 0 failed`; the release-only native installer acceptance run: `212 passed, 5 skipped, 0 failed`.
- `compileall` and `git diff --check` are persistent Phase 5.5.6 regression checks and passed in the full suite.
- Current-HEAD native Windows installer build/install/upgrade/uninstall smoke passed with Inno Setup 7.
- Accepted installer: `codemcp-remote-setup.exe`.
- Accepted installer SHA-256: `7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93`.
- The native installer acceptance harness remains in-tree but is release-only by default; set `CODEMCP_RUN_RELEASE_INSTALLER_ACCEPTANCE=1` to opt in, and optionally `CODEMCP_ISCC_PATH` for a non-standard Inno Setup location.
- Working tree was clean at each completed gate.

**STOP GATE:** Phase 5.5.7 is NOT STARTED and must not begin without explicit user instruction.

Then STOP.

---

## Phase 5.5.7 — Final ChatGPT + external OAuth + clean Windows acceptance

**Status: IN PROGRESS — RFC 9728 repository fix and managed reinstall harness fix are PASS/READY; replacement installer candidate remains valid; live installed binary requires reinstall; LIVE external deployment/ChatGPT acceptance remains BLOCKED/PENDING.**

### 2026-08-26 RFC 9728 live blocker

The live installed binary returned `404` for
`GET /.well-known/oauth-protected-resource/mcp` and returned `401` from `GET /mcp` with only
`WWW-Authenticate: Bearer`. The repository now derives and serves RFC 9728 Protected Resource
Metadata from the configured canonical resource and includes the same metadata URL in Bearer
challenges without changing `mcp-rs-verification-v1` validation or fail-closed semantics.

Repository fix status is **PASS**. Candidate `ead65ece...` contained the RFC 9728 fix and passed
packaged-runtime smoke, but its first live reinstall rerun exposed a persisted `phase5-clean` project
registration: `project add` failed with `project already exists`. The harness rerun fix adds an explicit
expected-root ownership-checked `project remove` operation and a fresh-baseline rebuild path. Replacement
installer candidate SHA-256 is
`b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e`; its packaged CLI and RFC 9728
smokes pass. The installed old SHA-256
`7716e7bf7c5ceff536744f6342f1e7f6615eed770c7114c955a2bc70c33e6a93` remains **STALE**. Do not mark
the live discovery item PASS until the replacement candidate completes clean `Prepare`/`Start` and both
public curl checks return the expected metadata and challenge.

### 2026-08-26 Prepare rerun ownership blocker

The clean acceptance harness persists project registrations under
`%LOCALAPPDATA%\codemcp-remote\config\projects.toml`; `init` intentionally preserves that file and
the product CLI previously exposed only `project add`. Therefore a later Prepare could recreate the
disposable directory but still fail at `project add phase5-clean`. The new formal remove operation
requires `--expected-root`, returns an explicit `not-found` first-run result, removes only an exact
matching registration, and fails closed on a different root. Prepare then removes only its fixed
acceptance project subtree and creates a new Git baseline. It never deletes DPAPI transport/auth
credentials; custom project roots are rejected rather than managed automatically.

This is a harness rerun fix **READY**, not LIVE acceptance. The new candidate has not yet been used by
the user for Prepare/Start, and no Live endpoint or ChatGPT OAuth proof is claimed.

### 2026-08-26 managed reinstall ownership blocker

The next live rerun stopped before project reset because the clean-machine harness rejected any existing
production AppId installation. This was too broad: it could not distinguish the harness-owned acceptance
installation from an unrelated user installation on the same Windows account.

The managed-reinstall repository fix is **READY**. `Prepare` now permits first install when the AppId is
absent, or a same-AppId upgrade only when all non-secret ownership evidence matches the fixed acceptance
contract: the default install directory, fixed app root, `phase=5.5.7`, `phase5-clean`, the disposable
project root, selected transport, and configured Cloudflare resource/issuer values. Missing or corrupt
state, a different install directory/root, or any mismatched resource configuration remains fail-closed.
The harness stops the existing runtime through the formal packaged lifecycle command before invoking
Inno Setup with `/NOSTOPLIFECYCLE`, verifies the installed executable against the packaged checksum
manifest, and records previous/current installer and executable identity fields. It does not remove
DPAPI secrets, user runtime data, or the disposable project outside the existing fixed reset path.

Only `scripts/validate-clean-windows-release.ps1`, its harness tests, and acceptance documentation
changed in this fix. The packaged runtime/Inno payload did not change, so candidate
`b0c99f7b8aa8a78076c7645f5ce073b118c66fa03b40a8870a8b542dd869a36e` remains the current installer
candidate. The release-candidate ZIP was refreshed with the new harness script at
`9053041a675fe68baf1b5a1145ade0612508dfc50bcf9e8b5e8e87cba8322c28`. No user Prepare/Start/Cleanup
or public endpoint verification has been run after this fix.

### Objective

Replace the interrupted Phase 5 acceptance with the final proof of Cloudflare transport plus independent MCP OAuth authentication.

### Disposable project template

The harness should create:

```text
README.md
PHASE5_ACCEPTANCE.txt
pyproject.toml
```

Do **not** create `codemcp.toml`.

`pyproject.toml` is only a static marker allowing the Bridge to resolve the built-in Python project profile and generated fixed command catalog.

The clean-machine acceptance does not execute Python and does not require Python to be installed.

### Local clean-machine contract

Prove:

- exact installer SHA-256;
- install succeeds;
- worker mode = local;
- Python invisible on isolated PATH;
- uv invisible;
- pwsh invisible;
- Git available;
- cloudflared bundled and found;
- Cloudflare tunnel secret recovered from DPAPI;
- Bridge health OK;
- Cloudflare provider health OK;
- Bridge remains loopback-only;
- generic OAuth resource-server configuration is structurally valid;
- no auth-server private signing key, user database, client database or refresh-token state exists in the codemcp-remote runtime.

### ChatGPT + independent auth-server contract

Create/connect the ChatGPT custom MCP/plugin using the final OAuth configuration backed by the independent `mcp-auth-server`.

Prove:

1. ChatGPT discovers the intended auth server/resource metadata.
2. OAuth authorization succeeds.
3. CIMD/static-public-client/redirect-URI behavior matches the **currently frozen `mcp-auth-server` trust profile**. Public DCR is disabled; if ChatGPT cannot interoperate through CIMD or an explicitly pre-registered public client, record a blocker rather than re-enable DCR merely to pass acceptance.
4. PKCE S256 behavior matches the frozen auth-server contract.
5. a credential issued under the exact `mcp-auth-server` Resource Server Verification Contract version used for acceptance is accepted for this MCP resource.
6. a credential for the wrong canonical audience/resource is rejected.
7. expired, revoked, inactive or otherwise invalid authorization is rejected without Git state change.
8. refresh/session renewal behavior works as specified by the auth-server interoperability contract and does not weaken Resource Server validation.
9. tool discovery succeeds.
10. `project_open phase5-clean` succeeds.
11. `project_status.development_ready == true`.
12. `file_read PHASE5_ACCEPTANCE.txt` returns the expected marker.
13. initial Git state is baseline + clean.
14. deterministic remote mutation succeeds.
15. resulting checkpoint is inspected.
16. identical replay returns the original operation/checkpoint without second execution.
17. `checkpoint_restore` uses the canonical request hash over:

```text
project_id
session_id
checkpoint_id
expected_head
```

18. approval flow is completed normally.
19. CAS restore returns to the original baseline.
20. final `git_status` exactly matches baseline HEAD and clean worktree.
21. if scopes were proven in Phase 5.5.0, at least one allowed and one denied scope boundary is exercised.
22. Cloudflare identity headers are not required for the authenticated MCP path.
23. cleanup/uninstall succeeds.

### Completion criteria

Phase 5.5 is PASS only when all local, external OAuth, remote mutation, idempotency, rollback, negative-auth, and cleanup evidence is recorded.

The acceptance record must identify the exact `mcp-auth-server` build/version/commit used, without making that project part of this repository's packaged payload.

After PASS:

- update Phase 5 release evidence;
- mark Cloudflare as the recommended transport;
- document `mcp-auth-server` as a compatible external authorization-server dependency rather than an embedded component;
- keep OpenAI Tunnel as optional/compatibility transport;
- freeze `v0.1.0` packaging only after explicit user approval.

Then STOP.

## 6. Expected file impact

Likely additions:

```text
bridge/src/codemcp_bridge/transports/
bridge/src/codemcp_bridge/auth/
bridge/tests/test_cloudflare_transport.py
bridge/tests/test_oauth_resource_auth.py
docs/reports/compatibility/mcp-auth-server-chatgpt-spike.md
docs/guides/cloudflare-tunnel-setup.md
docs/guides/external-mcp-auth-setup.md
```

Likely modifications:

```text
bridge/src/codemcp_bridge/lifecycle.py
bridge/src/codemcp_bridge/main.py
bridge/src/codemcp_bridge/mcp_server.py
bridge/src/codemcp_bridge/mcp_transport.py
bridge/src/codemcp_bridge/settings.py
scripts/build-windows-exe.ps1
scripts/build-windows-installer.ps1
scripts/codemcp-remote.iss
scripts/validate-clean-windows-release.ps1
docs/architecture/architecture.md
docs/architecture/security-model.md
docs/architecture/threat-model.md
docs/guides/operations-runbook.md
README.md
```

The independent `mcp-auth-server` repository is not part of this file-impact list. Its implementation plan, tests, storage model and release artifacts belong to that project.

This list is planning guidance, not authorization to modify all listed files.

## 7. Risk register

### High — access token is accepted without binding it to the intended MCP resource

Mitigation:

- `mcp-auth-server` Phase 4.1 freezes issuer/resource/audience semantics and reusable wrong-resource test vectors;
- Phase 5.5.0 verifies those semantics through the ChatGPT interoperability path;
- Bridge validates the exact canonical resource binding before any tool dispatch;
- negative tests cover credentials issued for a different MCP server;
- no fallback to token-presence-only authentication.

### High — authorization-server internals leak into codemcp-remote

Mitigation:

- protocol-only integration boundary;
- no shared database/filesystem/private signing keys;
- no auth-server package imports;
- acceptance runs the auth server as an independently versioned external dependency.

### High — public MCP endpoint bypasses authentication

Mitigation:

- Bridge resource-server auth is enforced independently of Cloudflare;
- Cloudflare is treated only as transport;
- direct requests without a valid token fail closed;
- Bridge remains loopback-only behind the outbound tunnel.

### Medium — transport refactor regresses OpenAI Tunnel

Mitigation:

- provider abstraction first;
- existing OpenAI behavior frozen by regression tests;
- no removal during Phase 5.5.

### Medium — cloudflared credential leakage

Mitigation:

- DPAPI storage;
- explicit log redaction;
- no plaintext config;
- packaging scan;
- negative tests.

### Medium — OAuth/DCR/CIMD behavior differs between ChatGPT and mcp-auth-server

Mitigation:

- Phase 5.5.0 is a mandatory live interoperability spike;
- exact redirect URI, PKCE, DCR/CIMD, token and refresh contracts are captured;
- no production auth integration before the contract is proven.

### Medium — auth-server signing-key rotation or outage breaks MCP access

Mitigation:

- bounded JWKS cache and rotation tests for signed tokens;
- explicit timeout/fail-closed design for opaque-token introspection if used;
- doctor separates offline structural checks from live network diagnostics;
- no insecure fail-open path.

### Low — Python profile marker is misunderstood as a Python runtime dependency

Mitigation:

- acceptance documentation explicitly states it is static detection metadata only;
- clean-machine PATH gate still proves Python is absent.

## 8. Rollback strategy

At every sub-phase:

- code changes are isolated in Git commits;
- existing OpenAI transport remains available;
- do not migrate user runtime state destructively;
- config migration must preserve or back up the previous provider config;
- installer uninstall continues to preserve user data/secrets by default;
- the external auth server remains independently deployable and removable.

If the independent auth-server interoperability spike fails in Phase 5.5.0, stop the generic OAuth integration work. Do not embed a one-off OAuth server into `codemcp-remote` and do not weaken authentication to a static shared secret merely to continue.

If Cloudflare transport itself is sound but OAuth interoperability is not, Cloudflare may remain a future transport option, but it must not become the recommended authenticated release path until the auth contract is proven.

## 9. Estimated engineering size

Relative size inside `codemcp-remote`:

```text
Phase 5.5.0  small/medium
Phase 5.5.1  medium
Phase 5.5.2  medium
Phase 5.5.3  medium/high
Phase 5.5.4  medium
Phase 5.5.5  small/medium
Phase 5.5.6  medium
Phase 5.5.7  medium
```

The independent `mcp-auth-server` has its own engineering estimate and phase plan and must not be counted as hidden work inside these estimates.

The highest-risk work in this repository is correct OAuth resource-server validation and interoperability, not the Cloudflare Tunnel process itself.

The Bridge, native worker, Git safety, checkpoint, idempotency, and approval architecture should remain largely unchanged.

## 10. Execution rule

Implementation order is fixed:

```text
5.5.0 external MCP auth + HTTP interoperability spike
  ->
5.5.1 transport abstraction
  ->
5.5.2 Cloudflare provider
  ->
5.5.3 generic OAuth resource-server integration
  ->
5.5.4 CLI/config/migration/doctor
  ->
5.5.5 installer integration
  ->
5.5.6 security/regression gate
  ->
5.5.7 final ChatGPT + external OAuth + clean-machine acceptance
```

Cross-project dependency rule:

```text
mcp-auth-server may develop in parallel
          |
          v
Phase 5.5.0 requires a testable auth-server endpoint
          |
          v
No production auth integration before 5.5.0 PASS
```

Do not skip forward.

After completing each phase:

1. report changed files;
2. report tests/evidence;
3. report commit;
4. report remaining blockers;
5. STOP and wait for explicit `继续`.
