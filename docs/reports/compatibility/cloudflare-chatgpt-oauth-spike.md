# Phase 5.5.0 — Cloudflare + ChatGPT OAuth Compatibility Spike

Status: **IN PROGRESS — protocol contract researched; live end-to-end proof pending**

Date: 2026-08-25

Repository: `codemcp-remote`

## 1. Objective

Prove the external HTTP/OAuth contract before any production transport refactor.

This spike does not modify Bridge, Git/checkpoint semantics, tool definitions, mutation policy, or packaged runtime behavior.

## 2. Evidence classes

- **DOC** — confirmed by current official Cloudflare/OpenAI documentation.
- **UI** — confirmed by the current ChatGPT custom MCP/plugin UI observed during this spike.
- **LIVE** — must be demonstrated end to end against a disposable Cloudflare-protected endpoint before Phase 5.5.0 can PASS.

A documentation result is sufficient to define the expected contract, but it does not replace a required LIVE item from the execution plan.

## 3. Confirmed protocol contract

### 3.1 HTTP MCP endpoint

Cloudflare Access supports protecting MCP server applications and MCP server portals exposed through an HTTP URL such as:

```text
https://mcp.example.com/mcp
```

Cloudflare Tunnel can publish a loopback origin while the Bridge remains bound to `127.0.0.1`.

Expected codemcp-remote origin remains:

```text
http://127.0.0.1:46200/mcp
```

Evidence: **DOC**

### 3.2 Managed OAuth challenge and discovery

For non-browser clients with Managed OAuth enabled, Cloudflare Access returns an OAuth challenge with:

```text
HTTP 401
WWW-Authenticate: ...
```

The challenge points the client at OAuth resource/authorization-server discovery.

Cloudflare documents an RFC 8414 / RFC 9728 compatible authorization-server metadata path and an authorization code flow.

Evidence: **DOC**

### 3.3 Dynamic client registration

Cloudflare Managed OAuth supports Dynamic Client Registration when enabled.

The authorization-server metadata can expose:

```text
registration_endpoint
authorization_endpoint
token_endpoint
```

The documented authorization-server capabilities include:

```text
authorization_code
refresh_token
PKCE S256
public clients (token endpoint auth method: none)
```

Cloudflare Access allows an administrator to restrict dynamically registered client redirect URIs.

Evidence: **DOC**

### 3.4 ChatGPT client metadata / callback

The ChatGPT custom MCP/plugin UI observed for this spike exposes:

```text
CIMD client metadata URL:
https://chatgpt.com/oauth/client.json

callback URL:
https://chatgpt.com/connector_platform_oauth_redirect
```

The UI also supports selecting CIMD vs other client-registration modes and configuring OAuth endpoints/scopes.

These exact values must be copied from the live ChatGPT UI at execution time; do not assume they are permanently stable.

Evidence: **UI**

### 3.5 Token and refresh behavior

Cloudflare Managed OAuth issues an opaque client access token rather than exposing the Access JWT directly to the OAuth client.

Cloudflare resolves that opaque token at the edge and forwards an origin assertion.

Cloudflare documents refresh-token based renewal and recommends a short access-token lifetime with a longer grant/session duration for CLI/agent use cases.

Recommended spike settings:

```text
access token lifetime: 5m
grant session duration: 24h
```

The final product value may use a longer grant session after acceptance.

Evidence: **DOC**

### 3.6 Origin identity assertion

For authenticated requests, the origin receives:

```text
Cf-Access-Jwt-Assertion: <signed Access JWT>
```

The origin must validate that JWT.

Public signing keys:

```text
https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
```

Expected JWT validation contract:

```text
issuer:
https://<team>.cloudflareaccess.com

audience:
Cloudflare Access Application Audience (AUD) tag
```

The exact live `iss` formatting and `aud` value must be captured from the disposable application before implementation.

Evidence: **DOC**, exact values require **LIVE**

### 3.7 Authentication vs authorization

Managed OAuth proves user identity and Access policy membership.

No current Managed OAuth documentation proves arbitrary application-defined scopes such as:

```text
codemcp:read
codemcp:write
codemcp:execute
codemcp:checkpoint
```

Therefore Phase 5.5.0 does **not** authorize a scope-to-tool mapping.

Current design decision unless LIVE evidence proves otherwise:

```text
Cloudflare Access = authentication / coarse access policy
Bridge policy       = authoritative tool authorization
```

Evidence: **DOC review; custom scope enforcement NOT PROVEN**

### 3.8 401 / 403 behavior

Expected unauthenticated Managed OAuth behavior:

```text
401 + WWW-Authenticate OAuth challenge
```

Cloudflare Access policy denial can produce a forbidden response, but Cloudflare documents that policy-denied cases may use `401` or `403` depending on the policy determination.

Therefore the product must not depend on a single universal `403` for every authorization denial.

Exact behavior for the selected disposable policy remains a LIVE test.

Evidence: **DOC**, selected-policy behavior requires **LIVE**

## 4. ChatGPT-side contract

OpenAI documents the custom MCP/app setup flow as:

1. provide the remote MCP endpoint;
2. choose authentication;
3. complete OAuth when prompted;
4. scan tools;
5. create/enable the app;
6. invoke read and write tools according to the app's available actions and permission controls.

OpenAI also warns that refresh-token support is required for durable OAuth connectivity.

The current ChatGPT UI independently confirms OAuth endpoint configuration, CIMD/client-registration configuration, callback URL, and scope fields.

Evidence: **DOC + UI**

## 5. PASS / FAIL matrix

| # | Requirement | Current result | Evidence / remaining proof |
|---|---|---|---|
| 1 | ChatGPT reaches HTTPS `/mcp` through Cloudflare | PENDING LIVE | Must use disposable hostname/tunnel |
| 2 | Streamable HTTP MCP tool discovery | PENDING LIVE | Must complete ChatGPT Scan Tools |
| 3 | OAuth discovery behavior | CONTRACT PASS / LIVE PENDING | Cloudflare documents 401 + discovery |
| 4 | CIMD/client metadata behavior | UI PASS / LIVE PENDING | ChatGPT exposes CIMD metadata URL |
| 5 | Dynamic client registration | CONTRACT PASS / LIVE PENDING | Cloudflare DCR supported when enabled |
| 6 | Authorization callback | UI PASS / LIVE PENDING | Exact callback shown by ChatGPT UI |
| 7 | Token issuance + refresh | CONTRACT PASS / LIVE PENDING | Refresh supported; must test reconnect |
| 8 | Origin header/JWT | CONTRACT PASS / LIVE VALUE PENDING | `Cf-Access-Jwt-Assertion` documented |
| 9 | JWT issuer/audience | CONTRACT PASS / LIVE VALUE PENDING | team issuer + application AUD |
| 10 | 401 vs 403 | CONTRACT PARTIAL / LIVE PENDING | 401 challenge confirmed; denial varies |
| 11 | Read tool call | PENDING LIVE | Must call safe disposable read tool |
| 12 | Write tool call | PENDING LIVE | Must call disposable write tool |
| 13 | Reconnect after refresh/session expiry | PENDING LIVE | Use 5m token for spike |
| 14 | Meaningful custom OAuth scopes | NOT PROVEN | Do not use for tool authorization |

Phase 5.5.0 is **not PASS yet**.

## 6. Disposable live-test configuration contract

Use a dedicated hostname and do not reuse a production MCP hostname.

Example only:

```text
public MCP URL:
https://codemcp-spike.example.com/mcp

Cloudflare Tunnel origin:
http://127.0.0.1:46200

Bridge MCP path:
/mcp
```

Cloudflare Access application:

```text
application type:
self-hosted / MCP server application

Managed OAuth:
enabled

Dynamic client registration:
enabled

Allowed redirect URI:
use the exact callback URL shown by ChatGPT
(current observed value:
 https://chatgpt.com/connector_platform_oauth_redirect)

Access policy:
Allow only the spike tester identity

Access token lifetime:
5m

Grant session duration:
24h
```

ChatGPT custom MCP/plugin:

```text
Server URL:
https://codemcp-spike.example.com/mcp

Authentication:
OAuth

Client registration:
CIMD

CIMD metadata:
use the value shown by ChatGPT
(current observed value:
 https://chatgpt.com/oauth/client.json)

Scopes:
do not use custom codemcp:* scopes as an authorization boundary during this spike
```

Secrets, tunnel credentials, OAuth tokens, Access JWTs, and client secrets must never be copied into this report.

## 7. Live test sequence

The live test must execute in this order:

1. Start the existing Bridge on the development machine.
2. Publish only the loopback Bridge through a dedicated Cloudflare named tunnel.
3. Protect the spike hostname with a dedicated Access application.
4. Enable Managed OAuth and DCR.
5. Confirm an unauthenticated request produces `401` plus OAuth discovery metadata.
6. Create/scan the ChatGPT custom MCP/plugin.
7. Complete the browser authorization flow.
8. Confirm MCP tool discovery.
9. Confirm a safe read operation.
10. Confirm one deterministic write operation on a disposable Git project.
11. Confirm the origin receives a Cloudflare Access assertion for the authenticated MCP request without logging the raw token.
12. Record live `iss` and `aud` values with identity/token material redacted.
13. Wait for or force the 5-minute access-token refresh boundary and confirm reconnection/tool use.
14. Deny the tester in Access and record the denial response without changing Git state.
15. Restore the Allow policy.
16. Decide whether any stable useful application scope is actually present; otherwise keep Bridge policy authoritative.
17. Remove the spike hostname/application/tunnel route or clearly mark them disposable.

## 8. Stop conditions

Immediately stop Phase 5.5 if the live test proves any of these:

- ChatGPT cannot connect to the Cloudflare-protected Streamable HTTP MCP endpoint;
- Managed OAuth cannot complete reliably;
- the origin does not receive an independently verifiable Cloudflare Access identity assertion;
- a write-tool call cannot traverse the selected ChatGPT OAuth flow.

No production transport abstraction may start until all four conditions are cleared.

## 9. Current blocker

The remaining work requires an actual user-owned Cloudflare named tunnel / Access application and an interactive ChatGPT OAuth authorization.

No repository credential or Cloudflare secret is required or requested by this report.

Phase 5.5.0 remains **IN PROGRESS** until the LIVE rows in the matrix are executed and recorded.

## 10. Sources consulted

- Cloudflare One: Managed OAuth
- Cloudflare One: Validate JWTs
- Cloudflare One: Secure MCP servers / MCP server portals
- Cloudflare One: Access policies
- OpenAI Help Center: Developer mode and MCP apps in ChatGPT

Source facts were checked on 2026-08-25. Live product behavior remains authoritative for the acceptance matrix.
