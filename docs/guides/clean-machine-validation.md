# Clean Windows Release Validation

Use this guide to validate the packaged `codemcp-remote` Windows release on a clean Windows 11 x64-compatible host or VM.

## Scope

The clean-machine gate verifies that the release candidate can:

1. install without the source repository, Python, `uv`, PowerShell 7, or WSL2 as runtime dependencies;
2. use the bundled transport clients and native local codemcp worker;
3. initialize a writable runtime home;
4. configure the recommended Cloudflare network-trust profile without bundling operator credentials;
5. store runtime secrets with Windows DPAPI;
6. create and operate on a disposable local Git repository;
7. start and stop the managed Bridge/Tunnel lifecycle;
8. preserve runtime/user data across upgrade and uninstall.

Git for Windows is an intentional runtime prerequisite and is not redistributed by the installer.

## Files in the release candidate

The release candidate contains:

- `codemcp-remote-setup.exe`
- `SHA256SUMS.txt`
- `release-manifest.json`
- `validate-clean-windows-release.ps1`
- `CLEAN-MACHINE-VALIDATION.md`
- `LICENSE`

Before installation, verify the archive and installer checksums against the files shipped with the release candidate.

## Recommended profile: 5.5.7A

Profile `5.5.7A` uses:

- transport: `cloudflare`
- authentication: `none`
- network trust: `cloudflare-chatgpt`
- a user-owned Cloudflare account, domain, and tunnel
- `TUNNEL_TOKEN`, stored with Windows DPAPI

Network trust is not user identity. It restricts the accepted network path but does not identify a ChatGPT account, Workspace, conversation, or end user.

Use a hostname you control. Examples in this guide use `mcp.example.com`.

### Secret handling

Do not place tunnel tokens or OAuth verification secrets in this document, source files, command history, or release artifacts.

Set the tunnel token only in the current PowerShell process before `Prepare`:

```powershell
$env:TUNNEL_TOKEN = "<set locally>"
```

The validation harness stores the token with Windows DPAPI and removes the plaintext process value before its final runtime checks.

## Prepare

From the extracted release-candidate directory:

```powershell
$installerHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath ".\codemcp-remote-setup.exe"
).Hash.ToLowerInvariant()

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File ".\validate-clean-windows-release.ps1" `
  -Action Prepare `
  -AcceptanceProfile 5.5.7A `
  -InstallerPath ".\codemcp-remote-setup.exe" `
  -ExpectedInstallerSha256 $installerHash `
  -Transport cloudflare `
  -PublicUrl "https://mcp.example.com/mcp" `
  -AllowedHost "mcp.example.com"
```

Expected result:

```json
{
  "status": "ready-for-start",
  "acceptance_profile": "5.5.7A",
  "worker_mode": "local",
  "transport": "cloudflare",
  "auth_mode": "none",
  "network_trust_mode": "cloudflare-chatgpt",
  "identity_level": "network-only",
  "transport_secret_source": "windows-dpapi"
}
```

`Prepare` must fail closed if the installer checksum is wrong, the host is unsupported, required files are missing, the existing installation is not an owned validation installation, or runtime isolation cannot be established.

## Start

After `Prepare` succeeds:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File ".\validate-clean-windows-release.ps1" `
  -Action Start `
  -AcceptanceProfile 5.5.7A `
  -Transport cloudflare `
  -PublicUrl "https://mcp.example.com/mcp" `
  -AllowedHost "mcp.example.com"
```

Expected result includes:

```json
{
  "status": "ready-for-remote-verification",
  "acceptance_profile": "5.5.7A",
  "bridge_health": "ok",
  "tunnel_health": "ok"
}
```

At this point, complete the authorized remote connector verification against the clean-machine deployment. Do not publish or record tunnel tokens, DPAPI blobs, OAuth secrets, local user paths, or private deployment identifiers as release evidence.

## Cleanup

After remote verification:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File ".\validate-clean-windows-release.ps1" `
  -Action Cleanup `
  -AcceptanceProfile 5.5.7A
```

Cleanup must stop the managed lifecycle and uninstall the application without deleting preserved runtime/user data.

Use `-Action Reset` only when intentionally resetting harness-managed acceptance state.

## Optional profile: 5.5.7B

Profile `5.5.7B` is the optional OAuth Resource Server interoperability path. Use it only when repeating the external OAuth validation contract.

It requires operator-provided values such as:

- `AuthorizationServerIssuer`
- `CanonicalResourceUri`
- `ValidationResourceId`
- `CODEMCP_RS_VERIFICATION_SECRET`

The verification secret must be provided through the current process environment and stored with Windows DPAPI. It must never be bundled in the release candidate.

## PASS criteria

The clean-machine gate is PASS only when all required steps for the selected profile succeed on a clean or harness-owned Windows environment and the exact production installer under test matches its expected SHA-256.

An isolated installer smoke performed on a development machine is useful pre-release evidence, but it does not replace this production clean-machine validation.
