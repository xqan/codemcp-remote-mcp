# Windows build, install, and use

This guide covers the complete Windows flow from source checkout to a working ChatGPT Connector.

## 1. Build prerequisites

Build on Windows 11 x64 with:

- Git for Windows;
- Python 3.12+;
- `uv`;
- PowerShell 7 (`pwsh`);
- Inno Setup 7 when building the installer.

Verify:

```powershell
git --version
python --version
uv --version
pwsh --version
```

Install Inno Setup 7 if needed:

```powershell
winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
```

## 2. Build the packaged EXE

From the repository root:

```powershell
pwsh -File .\scripts\build-windows-exe.ps1
```

Default output:

```text
.local\dist\codemcp-remote\
  codemcp-remote.exe
  codemcp-start.cmd
  codemcp-stop.cmd
  config\
  LICENSE
  SHA256SUMS.txt
  ...
```

The build performs the scoped formatting/tests and packaged-runtime smoke checks before reporting success.

To build into a custom location:

```powershell
pwsh -File .\scripts\build-windows-exe.ps1 `
  -DistDir "D:\build\codemcp-remote-dist" `
  -WorkDir "D:\build\codemcp-remote-work"
```

## 3. Build the Windows installer

Recommended:

```powershell
pwsh -File .\scripts\build-windows-installer.ps1 `
  -ISCCPath "C:\Program Files\Inno Setup 7\ISCC.exe"
```

If Inno Setup is installed elsewhere, pass the real `ISCC.exe` path.

Default output:

```text
.local\installer-dist\
  codemcp-remote-setup.exe
  SHA256SUMS.txt
```

The installer build also stages the pinned remote transports and rejects runtime/secret material from the installer payload.

Verify the generated checksum:

```powershell
Get-FileHash .\.local\installer-dist\codemcp-remote-setup.exe -Algorithm SHA256
Get-Content .\.local\installer-dist\SHA256SUMS.txt
```

## 4. Install

Run:

```text
codemcp-remote-setup.exe
```

A user-owned writable directory is recommended. For example:

```text
D:\codemcp-remote
```

The packaged application is self-contained apart from the explicit Git for Windows prerequisite.

A typical installed layout is:

```text
D:\codemcp-remote\
  codemcp-remote.exe
  codemcp-start.cmd
  codemcp-stop.cmd
  cloudflared.exe
  tunnel-client.exe
  config\
  data\
  secrets\
  ...
```

## 5. Default home behavior

For the packaged Windows EXE, the default `codemcp` home is the directory containing `codemcp-remote.exe`.

Example:

```text
EXE:
D:\codemcp-remote\codemcp-remote.exe

default home:
D:\codemcp-remote
```

Therefore normal installed commands no longer need:

```powershell
--home D:\codemcp-remote
```

The normal override order is:

```text
explicit --home
  -> CODEMCP_HOME environment variable
  -> packaged EXE directory
```

`--app-root` remains a legacy compatibility option and should not be used for a normal new installation.

Source-mode development keeps its existing source/development fallback unless `--home` or `CODEMCP_HOME` is supplied.

## 6. First-time Cloudflare initialization

The recommended personal profile is:

```text
ChatGPT
  -> OpenAI/ChatGPT Connector egress
  -> Cloudflare WAF/IP allowlist
  -> Cloudflare Tunnel
  -> codemcp-remote
```

It uses `Authentication = No authentication` in ChatGPT, while Cloudflare provides the external network restriction and the Bridge retains Host/project/operation/Git safety controls.

Assume the installed EXE is:

```powershell
$exe = "D:\codemcp-remote\codemcp-remote.exe"
```

Load the Cloudflare Tunnel token into the current process only:

```powershell
$env:TUNNEL_TOKEN = "<load from your secret manager>"
```

Initialize:

```powershell
& $exe init `
  --transport cloudflare `
  --public-url "https://mcp.example.com/mcp" `
  --auth-mode none `
  --network-trust cloudflare-chatgpt `
  --allowed-host mcp.example.com `
  --allowed-origin "https://chatgpt.com" `
  --store-transport-secret
```

Then remove the plaintext process value:

```powershell
$env:TUNNEL_TOKEN = $null
```

The token is stored with Windows DPAPI.

The runtime files are now under the installation/home directory, for example:

```text
D:\codemcp-remote\config\remote.toml
D:\codemcp-remote\config\bridge.toml
D:\codemcp-remote\config\projects.toml
D:\codemcp-remote\data\
D:\codemcp-remote\secrets\
```

## 7. Register or remove a project

Project registration is a **local administrative control-plane action**. Only the local CLI should add or remove project authorization. ChatGPT/MCP clients can use already registered projects, but they cannot add, remove, reload, or reconfigure registrations.

Add a project:

```powershell
& $exe project add my-project "D:\workspace\my-project"
```

A successful result includes:

```json
{
  "status": "ok",
  "project_id": "codemcp-remote",
  "reload": "automatic",
  "restart_required": false
}
```

If the Bridge is already running, the next authorization-sensitive request detects and validates the changed `projects.toml` automatically. You do **not** need to restart the Bridge, Tunnel, EXE, or ChatGPT Connector.

Remove a project with an explicit expected-root guard:

```powershell
& $exe project remove my-project `
  --expected-root "D:\workspace\my-project"
```

Removal is also automatically observed by the running Bridge. New access is denied immediately on the next request, and existing active sessions for the removed project are blocked. Re-adding the same project ID does not revive those old sessions.

Do not directly change the root of an existing `project_id`. The live registry rejects an in-place root redirect and keeps the last-known-good snapshot. To intentionally move an ID to another root, perform an explicit `remove`, allow that removal to be observed, then `add` the new root.

Check live registry state:

```powershell
& $exe doctor
& $exe status
```

`status`/`doctor` report sanitized registry metadata such as project count, generation, reload status, and safe error codes. They do not expose the registered project list, project roots, or command argv through the public health payload.

Only register repositories that ChatGPT is intended to access. Direct `projects.toml` editing should be reserved for trusted offline maintenance or recovery; normal authorization changes should use the local CLI.

## 8. Start

There are three equivalent installed workflows.

### Option A: double-click the EXE

After first-time initialization, double-click:

```text
codemcp-remote.exe
```

A frozen EXE launched with no command defaults to:

```text
start
```

The managed Bridge and Tunnel are started and the launcher exits after reporting the result.

### Option B: one-click start script

Double-click:

```text
codemcp-start.cmd
```

The script is installed beside the EXE and calls:

```text
codemcp-remote.exe start
```

It intentionally does not pass `--home`; the EXE directory is already the default home.

### Option C: PowerShell

```powershell
& $exe start
```

Verify:

```powershell
& $exe status
& $exe doctor
```

## 9. Stop

Double-click:

```text
codemcp-stop.cmd
```

or run:

```powershell
& $exe stop
```

Verify:

```powershell
& $exe status
```

The installer also adds Start Menu entries for Start, Stop, Doctor, the application folder, and Uninstall.

## 10. Configure Cloudflare

Create an IP List such as:

```text
chatgpt_connectors
```

Populate it from the official OpenAI/ChatGPT Connector egress manifest.

Protect the dedicated MCP hostname with a whole-host WAF rule equivalent to:

```text
(http.host eq "mcp.example.com" and not ip.src in $chatgpt_connectors)
```

Action:

```text
Block
```

Do not reproduce the IP trust decision inside Python using `X-Forwarded-For`, `CF-Connecting-IP`, or similar headers.

See [Cloudflare tunnel setup](cloudflare-tunnel-setup.md) for the detailed network-trust runbook.

## 11. Connect ChatGPT

Create a ChatGPT Connector with:

```text
URL:
https://mcp.example.com/mcp

Authentication:
No authentication
```

Scan tools and confirm the expected Bridge tool surface is discovered.

A safe first workflow is:

```text
project_open
-> project_status
-> git_status
-> file_read
```

Before mutation, verify the branch and worktree are exactly the state you expect.

## 12. Upgrade

Run the newer installer and select the same installation directory.

The lifecycle is stopped before replacement. Runtime configuration, data, and DPAPI-backed secrets are user state and are not intentionally removed by a normal upgrade.

After upgrade:

```powershell
& $exe doctor
& $exe start
```

## 13. Troubleshooting

### Double-click does not start

Open PowerShell in the installation directory:

```powershell
.\codemcp-remote.exe doctor
.\codemcp-remote.exe status
.\codemcp-remote.exe start
```

The command-line JSON output gives the actual lifecycle error.

### Configuration is not found

Confirm which home is active:

```powershell
.\codemcp-remote.exe doctor
```

For an installed EXE with no override, it should resolve to the EXE directory.

Check that these files exist:

```text
config\remote.toml
config\bridge.toml
config\projects.toml
```

### ChatGPT cannot connect

Check in this order:

1. `codemcp-remote.exe status`;
2. `codemcp-remote.exe doctor`;
3. Cloudflare Tunnel health;
4. Cloudflare Security Events;
5. WAF/IP List membership for the ChatGPT Connector source;
6. the exact public `/mcp` URL.

### Logs

With the modern installed-home layout, logs are under:

```text
data\logs\
```

Do not paste tunnel tokens, DPAPI blobs, approval tokens, OAuth tokens, or other credentials into issue reports or chat.
