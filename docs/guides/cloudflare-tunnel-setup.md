# Cloudflare Tunnel + ChatGPT Network Trust

Status: **PHASE H LIVE ACCEPTANCE COMPLETE — real ChatGPT access and Cloudflare BLOCK/ALLOW evidence are recorded; the optional stopped-Tunnel `1033` proof was intentionally skipped. Stable `v0.1.0` remains blocked by the separate Phase 6/7 and release-wide gates.**

This is the recommended v0.1.0 personal-deployment path. ChatGPT Connector uses `Authentication = No authentication`; Cloudflare Edge/WAF supplies the external network restriction, while `codemcp-remote` keeps its local project, operation, approval, checkpoint, Git CAS, restore, audit, and loopback security policies.

## Topology

```text
ChatGPT Connector
  Authentication = No authentication
        |
        v
OpenAI / ChatGPT Connector egress network
        |
        v
Cloudflare Edge/WAF IP allowlist
        |
        v
https://mcp.example.com/mcp
        |
        v
Cloudflare Tunnel
        |
        v
127.0.0.1:46200
        |
        v
codemcp-remote network-trusted Bridge
        |
        v
project / operation / approval / checkpoint / Git CAS / restore / audit
```

The Bridge must remain loopback-only. The Tunnel origin is `http://127.0.0.1:46200`; never change it to `0.0.0.0`.

## Trust model and limitation

The Cloudflare IP List is a **network trust boundary** / **ChatGPT egress network restriction**. It proves only that Cloudflare accepted a source address in the configured OpenAI/ChatGPT Connector egress range. It is not authentication, user identity, or strong identity. In particular, it does not identify:

- a specific ChatGPT user;
- a Workspace;
- an account; or
- a conversation.

Profile A therefore reports `identity_level = network-only`. OAuth Profile B is the optional advanced profile when a real subject/client/scope identity is required.

The IP policy belongs at Cloudflare Edge/WAF. Do not reproduce it in Python and do not authorize from `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `True-Client-IP`, or `Cf-Access-*`. HTTP headers are not a network boundary.

## 1. Create the Cloudflare Tunnel

In the user-owned Cloudflare account:

1. create or select a Cloudflare Tunnel;
2. add a dedicated hostname such as `mcp.example.com`;
3. route it to `http://127.0.0.1:46200`;
4. preserve the `/mcp` path used by the Connector; and
5. do not add a second public origin or a public health route.

The bundled `cloudflared` is a deployment/runtime dependency of the packaged release. Store its tunnel token through the product's DPAPI flow; do not put the token in Git, `remote.toml`, a plaintext `.env` file, logs, screenshots, or acceptance evidence.

## 2. Create and populate the Cloudflare IP List

Create an account-level Cloudflare IP List with the suggested name:

```text
chatgpt_connectors
```

Populate it with the current IPv4/IPv6 CIDR ranges from the official OpenAI-published ChatGPT Connector egress IP manifest. Record the manifest URL, retrieval date, and operator in deployment notes. Do not copy current ranges into this repository, Python source, PowerShell source, or `remote.toml`; the ranges are expected to change.

The first supported provisioning mode is manual. If the official manifest has a stable documented JSON contract in the future, an administrative sync tool may validate a non-empty list, legal IPv4/IPv6 CIDRs, malformed-data rejection, diff, dry-run, and rollback before changing the list. The runtime must never hold a Cloudflare API token. Any future API automation must use a scoped token limited to the required IP List/WAF operations, read the token only from an environment variable, and never print or persist it. Do not use a Global API Key.

## 3. Create the whole-host WAF rule

For the dedicated hostname, create a Cloudflare WAF Custom Rule equivalent to:

```text
(
  http.host eq "mcp.example.com"
  and not ip.src in $chatgpt_connectors
)
```

Action: `Block`.

Protect the whole hostname rather than only `/mcp`. This also covers `/mcp`, `/.well-known/oauth-protected-resource/*`, and future public endpoints. Treat `/healthz` separately: it is a lifecycle probe and should not be exposed as a public application interface. If the Tunnel provider cannot keep it private, do not claim the public health route is protected by this runbook.

The WAF rule is the IP enforcement boundary. A request blocked there must not reach the Bridge. The Bridge's second boundary is exact Host validation from the actual `Host` authority. The configured host is:

```toml
[network_trust]
mode = "cloudflare-chatgpt"
allowed_hosts = ["mcp.example.com"]
allowed_origins = ["https://chatgpt.com"] # optional, if-present validation
```

`allowed_origins` may be empty. A missing `Origin` is accepted; when present, it must be an exact canonical HTTPS origin. Origin is auxiliary and is not authentication.

## 4. Initialize Profile A (No-Auth)

Use a packaged executable or source-mode `codemcp-remote` command. The example uses an explicit runtime home so all config, data, checkpoints, logs, runtime state, and DPAPI-backed secrets have one visible root:

```powershell
$exe = "$env:LOCALAPPDATA\Programs\codemcp-remote\codemcp-remote.exe"
$runtimeHome = Join-Path $env:LOCALAPPDATA "codemcp-remote"
$env:TUNNEL_TOKEN = "<read from your secret manager; do not paste into chat>"

& $exe init --home $runtimeHome `
  --transport cloudflare `
  --public-url "https://mcp.example.com/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host mcp.example.com `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret
$env:TUNNEL_TOKEN = $null

& $exe project add phase5-clean "D:\workspace\phase5-clean" --home $runtimeHome
```

Profile A does not require `CODEMCP_RS_VERIFICATION_SECRET`, does not install an OAuth bearer authenticator, and does not imply `authenticated_user=true`. It still requires all existing project and mutation safety policies.

`--home` overrides `CODEMCP_HOME`. The legacy `--app-root` option remains available for compatibility with older runtime state; it must not be used to bypass the selected trust policy.

## 5. Doctor and start

Run the profile-aware checks with the environment token already cleared:

```powershell
& $exe doctor --home $runtimeHome
& $exe start --home $runtimeHome
& $exe status --home $runtimeHome
```

The relevant `doctor` fields should be equivalent to:

```text
auth.mode: none
auth.status: ready
auth.oauth_secret_required: false
network_trust.mode: cloudflare-chatgpt
network_trust.status: ready
network_trust.allowed_hosts: [mcp.example.com]
identity_level: network-only
transport.provider: cloudflare
tunnel_token.source: windows-dpapi
```

For public Cloudflare transport, a missing trust policy or empty host list must fail closed with `PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST`. Pure loopback development is a separate local-only scenario and must not be confused with this public profile.

## 6. Normal-IP negative test

From a normal public network that is not in `$chatgpt_connectors`, request the dedicated hostname:

```powershell
curl.exe -i https://mcp.example.com/mcp
```

Expected result: Cloudflare WAF `403` (or the account's configured block response). Capture only non-sensitive status/timestamp evidence. The request must be blocked at Cloudflare and must not reach the Bridge. Do not substitute a Python response or a spoofed forwarding header for this test.

When the codemcp lifecycle is stopped, Cloudflare may present Tunnel-unavailable behavior such as `1033`, depending on account presentation. The Phase H acceptance intentionally skipped this disruptive proof to preserve the working Connector; it is a documented optional deviation and is not evidence required to reinterpret the already-recorded network-trust BLOCK/ALLOW result.

## 7. ChatGPT Connector Scan Tools

In ChatGPT, create or edit the Connector with:

```text
Authentication: No authentication
URL: https://mcp.example.com/mcp
```

Run `Scan Tools`. The scan must complete only after the WAF list contains the actual Connector egress range and the local lifecycle reports a healthy Bridge/Tunnel. A successful scan does not establish human identity; it establishes reachability through the configured network boundary.

Then execute the disposable-project contract in order: `project_open`, `file_read`, `git_status`, deterministic mutation, checkpoint, identical replay, approval, restore, and final clean baseline. Never use a real user repository for first acceptance.

## 8. Verify Cloudflare Security Events

In Cloudflare Security Events, verify separate non-sensitive entries:

```text
ordinary public source -> BLOCK
ChatGPT Connector source -> ALLOW
```

Record timestamps, rule action, hostname, and redacted request identifiers only. Do not record tokens, full request bodies, private project paths, or user content. An `ALLOW` event means network admission; it is not a ChatGPT user authentication event.

## 9. Updating the IP List

When OpenAI publishes a new official Connector manifest:

1. retrieve it through the documented official source;
2. verify the source and schema before editing the list;
3. calculate and review the old/new CIDR diff;
4. apply the change to `chatgpt_connectors`;
5. confirm the WAF rule still references the same list;
6. run the normal-IP negative test and a Connector Scan Tools check; and
7. retain the old list contents and change record long enough to support rollback.

Never update the Python runtime or `remote.toml` with current IP ranges. If the manifest is malformed or empty, reject the update and keep the last known-good Cloudflare list.

## 10. Rollback

For a bad list or WAF change, restore the last known-good IP List/rule in Cloudflare, verify the rule action in Security Events, and rerun both negative and Connector checks. Do not disable the WAF rule to restore availability. If the local runtime is unsafe, stop the project-owned lifecycle and remove the public hostname route; do not bind the Bridge publicly.

## 11. Optional OAuth advanced profile (5.5.7B)

Use Profile B for multi-user, enterprise, or deployments requiring a real subject/client/scope identity:

```toml
[auth]
mode = "oauth-resource-server"
issuer = "https://<authorization-server>"
canonical_resource_uri = "https://mcp.example.com/mcp"
validation_resource_id = "<resource-id>"
```

Profile B keeps the existing `mcp-rs-verification-v1` Resource Server contract, RFC 9728 protected-resource metadata/challenges, external `mcp-auth-server` authorization profile, OAuth subjects/clients/scopes, and DPAPI-backed verification secret handling. It is optional advanced security; OAuth was not abandoned or removed. Do not configure `auth.mode = "none"` with `network_trust.mode = "unrestricted"`, and do not describe the Profile A IP allowlist as user authentication.
