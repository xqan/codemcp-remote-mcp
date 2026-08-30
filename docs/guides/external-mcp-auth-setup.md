# External mcp-auth-server Setup for Phase 5.5.7B

Status: **OPTIONAL ADVANCED PROFILE — live interoperability is not required for the recommended 5.5.7A personal deployment**

The recommended v0.1.0 personal path uses ChatGPT Connector `Authentication =
No authentication` plus Cloudflare network trust. This guide documents the
preserved OAuth Resource Server profile for multi-user, enterprise, or
subject/client/scope-aware deployments. OAuth was moved to an optional advanced
profile; it was not abandoned or deleted.

`codemcp-remote` consumes `mcp-auth-server` only through the frozen protocol boundary. The authorization server remains independently deployed and independently versioned.

## Required deployment identity

The Phase 5.5.7 acceptance record must identify all of:

```text
mcp-auth-server version
mcp-auth-server Git commit
deployed canonical issuer
Resource Server Verification Contract version
canonical MCP resource URI
validation resource ID
```

Secrets are never recorded.

The repository-side baseline observed before live acceptance is:

```text
mcp-auth-server version: 0.1.0
Git commit: 83167bcc5834c357432236da7c69ceb91292047f
@cloudflare/workers-oauth-provider: 0.10.3
verification contract: mcp-rs-verification-v1
```

This baseline is **not yet a valid live deployment identity** by itself. At the time of this guide, the staging configuration still uses an `.invalid` issuer placeholder and the authorization-server repository has uncommitted Cloudflare deployment-profile changes. The final acceptance must use the exact clean/pinned commit actually deployed with a real issuer.

## Production-domain identity requirement

The current development identity adapter is intentionally restricted to localhost, loopback, or reserved `.test` hosts. It fails closed on a public production/staging domain even when accidentally enabled.

Therefore a real public Phase 5.5.7 issuer requires a formal identity/session adapter. Do not weaken this guard or enable development identity on the public acceptance hostname merely to complete the test.

## Resource provisioning

Provision the `codemcp-remote` resource in `mcp-auth-server` with:

```text
canonical resource URI = https://<mcp-host>/mcp
token audience         = exact canonical resource URI
authorization server   = https://<auth-host>
verification contract  = mcp-rs-verification-v1
```

Create a per-Resource verification credential and retain:

```text
validation resource ID
verification secret
```

The resource ID is public configuration for `codemcp-remote`. The verification secret is a runtime secret: supply it only through `CODEMCP_RS_VERIFICATION_SECRET` during clean-machine `Prepare`; the harness stores it using Windows DPAPI.

A credential issued for another resource or another path on the same origin must be rejected. `codemcp-remote` must not access auth-server KV/D1/provider storage, signing keys, user/client databases, or refresh-token state directly.

## ChatGPT client contract

Use the client policy actually frozen by the deployed `mcp-auth-server`; do not resurrect an older spike assumption.

Current trust profile:

- public OAuth client only;
- `token_endpoint_auth_method = none`;
- Authorization Code;
- PKCE S256;
- implicit flow disabled;
- CIMD supported;
- static public client supported;
- public DCR endpoint disabled;
- refresh-token rotation supported;
- exact Resource binding required across authorize, token exchange, and refresh.

When ChatGPT offers CIMD, use the current CIMD metadata URL shown by the ChatGPT UI. A previously observed value is `https://chatgpt.com/oauth/client.json`, but the live UI is authoritative.

Likewise, copy the OAuth redirect/callback URL from the live ChatGPT UI. A previously observed value is `https://chatgpt.com/connector_platform_oauth_redirect`; do not assume it is permanently stable.

If the live ChatGPT client insists on DCR and cannot operate through CIMD or an explicitly pre-registered public client, record an interoperability **FAIL/BLOCKER**. Do not enable DCR merely to force a pass without an explicit policy decision.

## Live verification requirements

The final external OAuth acceptance must prove:

1. authorization-server and protected-resource discovery resolve to the intended issuer/resource;
2. Authorization Code + PKCE succeeds;
3. the opaque bearer credential is accepted only through authenticated online validation at `POST {issuer}/mcp/resource-server/validate`;
4. wrong-resource, inactive, expired, revoked, malformed, or validation-service-failure paths fail closed and do not mutate Git state;
5. refresh/session renewal preserves the exact Resource binding;
6. ChatGPT can discover tools and execute the disposable read/write flow;
7. Cloudflare identity headers are unnecessary for authorization;
8. no meaningful scope-to-tool enforcement is claimed unless it is separately proven end to end.

## Secret boundary

Never place any of these in `codemcp-remote` source, config files, acceptance Markdown, ChatGPT messages, or command-line arguments:

```text
Cloudflare tunnel token
Resource Server verification secret
authorization-server admin token
OAuth access token
refresh token
authorization code
identity/session secrets
```

Only the tunnel token and Resource Server verification secret are needed by the `codemcp-remote` clean-machine harness, and both enter through process environment variables for one-time DPAPI storage.
