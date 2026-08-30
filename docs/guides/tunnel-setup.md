# OpenAI Secure MCP Tunnel compatibility setup

This guide documents the optional OpenAI Secure MCP `tunnel-client`
compatibility transport. The recommended v0.1.0 personal deployment uses
[Cloudflare Tunnel + ChatGPT network trust](cloudflare-tunnel-setup.md) with
ChatGPT Connector `Authentication = No authentication`.

The compatibility transport connects the private loopback Bridge to ChatGPT
through the OpenAI `tunnel-client`. It makes an
outbound HTTPS connection to the OpenAI control plane and forwards MCP
requests to the Bridge at `http://127.0.0.1:46200/mcp`.

The authoritative product documentation is the [OpenAI Secure MCP Tunnel
guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## Prerequisites

Create or obtain all of the following before starting the local flow:

- a `tunnel_id` from OpenAI Platform tunnel settings;
- a runtime `CONTROL_PLANE_API_KEY` whose principal has Tunnels Read + Use;
- `uv`, Python 3.12+, Git, and `tunnel-client` on `PATH`;
- ChatGPT developer-mode access for the target workspace.

Tunnel management permissions and ChatGPT developer-mode access are separate.
The tunnel must be associated with the Platform organization and ChatGPT
workspace that will use it.

## Local configuration

Copy the safe template, then replace only the tunnel ID:

```powershell
Copy-Item config/tunnel-profile.example.env config/tunnel-profile.local.env
```

Inject `CONTROL_PLANE_API_KEY` into the process from the Windows secret store
or an equivalent secret manager. Do not put the runtime key in the repository,
the local env file, a profile YAML file, PowerShell command history, or logs.

The wrapper deliberately requires the generated profile to use:

- `https://api.openai.com` or `https://mtls.api.openai.com` as the control plane;
- `env:CONTROL_PLANE_API_KEY` as the key reference;
- exactly one HTTP MCP target at `http://127.0.0.1:46200/mcp`;
- no stdio command and no non-loopback MCP target.

## Start the local path

Use two foreground terminals so either component can be stopped and diagnosed
independently.

Terminal 1:

```powershell
pwsh -File .\scripts\start-bridge.ps1
```

Terminal 2, on the first run:

```powershell
pwsh -File .\scripts\start-tunnel.ps1 -Initialize
```

Subsequent runs omit `-Initialize`:

```powershell
pwsh -File .\scripts\start-tunnel.ps1
```

`-Initialize` materializes a profile under `.local/tunnel-client`; it does
not create or modify an OpenAI-hosted tunnel. Use `-Force` only when replacing
that local profile intentionally.

## Diagnose readiness

Run the combined diagnostic while both processes are expected to be running:

```powershell
pwsh -File .\scripts\doctor.ps1
```

For a local-only check before starting `tunnel-client`:

```powershell
pwsh -File .\scripts\doctor.ps1 -SkipTunnel
```

The report checks Bridge configuration, Bridge `/healthz`, the profile
contract, `tunnel-client doctor --explain`, and tunnel-client `/healthz` and
`/readyz`. It never prints the runtime API key.

The tunnel client also exposes loopback-only operator endpoints:

```powershell
Invoke-WebRequest http://127.0.0.1:46201/healthz
Invoke-WebRequest http://127.0.0.1:46201/readyz
```

Readiness requires an authenticated control-plane connection and a reachable
Bridge MCP endpoint. Keep `tunnel-client run` alive while creating or testing
the ChatGPT app.

## Connect from ChatGPT

In ChatGPT developer mode:

1. Create a developer-mode app and choose **Tunnel** as the connection type.
2. Select the tunnel associated with the target ChatGPT workspace, or enter
   its valid `tunnel_id`.
3. Confirm that the Bridge tools are discovered.
4. Run the contract in [test_tunnel_contract.md](../../tests/e2e/test_tunnel_contract.md).

The Bridge remains the only MCP server visible to the tunnel. The downstream
codemcp worker is never configured as a tunnel target.

## Request and audit correlation

Each Bridge response contains both `request_id` and `operation_id`. After a
remote tool call, use `operation_status` with the same session to inspect the
persisted operation, checkpoint metadata, and audit events. Tunnel transport
does not replace Bridge authorization, approval, project registration, or
audit checks.

Some Streamable HTTP connectors reuse JSON-RPC request ID `0` for every call.
The Bridge preserves that value in the response for transport correlation but
derives a unique internal idempotency key for read-only operations so multiple
reads in one session remain independent. Mutation tools still require the
caller-provided `client_request_id` and `request_hash`.

## Failure handling

- If Bridge is down, `tunnel-client` may remain connected but MCP discovery and
  calls are not ready; restart Bridge and rerun `doctor.ps1`.
- If `tunnel-client` stops, ChatGPT calls fail until it reconnects. The Bridge
  does not replay a disconnected mutation.
- If the tunnel is not visible in ChatGPT, verify workspace association and
  Tunnels Read + Use permission before changing local code.
- Never replace this outbound tunnel with ngrok or another public tunnel.
