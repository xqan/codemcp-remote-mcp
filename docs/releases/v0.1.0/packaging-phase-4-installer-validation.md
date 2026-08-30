# Packaging Phase 4 — Windows Installer Validation

Date: 2026-08-24

## Objective

Produce a single per-user Windows installer named `codemcp-remote-setup.exe` from the already validated one-directory Phase 3 distribution. Phase 4 packages the Bridge, native codemcp worker, configuration templates, licenses, and a verified OpenAI `tunnel-client` binary. It does not perform the final fresh-machine release acceptance; that remains Packaging Phase 5.

## Installer design

- Builder: Inno Setup 7.
- Default install root: `%LOCALAPPDATA%\Programs\codemcp-remote`.
- Default runtime/data root remains `%LOCALAPPDATA%\codemcp-remote`.
- Administrative privileges are not required for the default install mode.
- The optional `addtopath` task is unchecked by default and only mutates the current user's PATH.
- PATH removal on uninstall removes only the exact path that setup previously added.
- User configuration, SQLite data, logs, tunnel profiles, and DPAPI secrets under `%LOCALAPPDATA%\codemcp-remote` are preserved by uninstall.
- Existing installed lifecycle processes are stopped through `codemcp-remote.exe stop` before an upgrade replaces binaries.
- Installer smoke uses an internal `/NOSTOPLIFECYCLE` switch so test install/uninstall cannot stop the currently active development lifecycle.
- Installer smoke refuses to run if the production AppId is already installed, preventing the smoke test from overwriting a real uninstall registration.

## Bundled OpenAI tunnel-client

Phase 4 pins the full OpenAI Secure MCP Tunnel client to `v0.0.12` for Windows amd64.

Upstream:

- Repository: `https://github.com/openai/tunnel-client`
- Release: `https://github.com/openai/tunnel-client/releases/tag/v0.0.12`
- License: Apache-2.0
- Artifact: `tunnel-client-v0.0.12-windows-amd64.zip`
- SPDX sidecar: `tunnel-client-v0.0.12-windows-amd64.spdx.json`
- Checksum manifest: `SHA256SUMS.txt`

`scripts/prepare-tunnel-client.ps1` downloads all three release assets from the exact versioned GitHub release, verifies the archive and SPDX sidecar against the published SHA-256 manifest, rejects unsafe ZIP paths and symlink entries, verifies the embedded SPDX evidence when present, verifies the binary version, and then copies only the required `tunnel-client.exe`, license, SPDX evidence, and upstream checksum manifest into the distribution.

The installer payload includes:

- `THIRD_PARTY_NOTICES.txt`
- `THIRD_PARTY\tunnel-client\LICENSE`
- `THIRD_PARTY\tunnel-client\tunnel-client-v0.0.12-windows-amd64.spdx.json`
- `THIRD_PARTY\tunnel-client\UPSTREAM-SHA256SUMS.txt`

The generated application `SHA256SUMS.txt` covers both `codemcp-remote.exe` and `tunnel-client.exe`.

## Payload safety

Before compiling the installer, `scripts/build-windows-installer.ps1` fails closed if the application payload contains any of:

- `tunnel-profile.local.env`
- `control-plane-api-key.dpapi`
- `state.json`
- `bridge.sqlite3`

The installer therefore contains executable/runtime files and templates only, never the developer machine's tunnel ID file, API key, DPAPI secret, lifecycle state, or SQLite runtime database.

## Build command

Install the official 64-bit Inno Setup 7 compiler if `ISCC.exe` is not already available:

```powershell
winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
```

Then build:

```powershell
pwsh -File .\scripts\build-windows-installer.ps1
```

The build performs:

1. the full Packaging Phase 3 executable build and smoke gates;
2. verified `tunnel-client v0.0.12` preparation;
3. installer payload secret/state checks;
4. Inno Setup compilation;
5. installer SHA-256 generation;
6. Authenticode state inspection;
7. isolated silent install;
8. installed `codemcp-remote.exe --version`;
9. installed `tunnel-client.exe --version`;
10. installed lifecycle `status` against an isolated app root;
11. silent uninstall and removal verification.

Expected output artifact:

```text
.local\installer-dist\codemcp-remote-setup.exe
.local\installer-dist\SHA256SUMS.txt
```

Expected final JSON includes:

```json
{
  "status": "ok",
  "phase": "4",
  "installer": "...\\codemcp-remote-setup.exe",
  "authenticode_status": "NotSigned",
  "tunnel_client_version": "v0.0.12",
  "tunnel_client_license": "Apache-2.0",
  "smoke": "passed"
}
```

`authenticode_status: NotSigned` is accepted in Phase 4 because this repository does not currently have a Windows code-signing certificate. A valid signature is also accepted. Any invalid or untrusted existing signature state fails the build.

## Known release risks

- The Phase 4 installer is expected to be unsigned unless a code-signing certificate is introduced later, so Windows SmartScreen may show an unknown-publisher warning.
- OpenAI `tunnel-client v0.0.12` has a current upstream report of antivirus false positives on some engines. The build must not disable or bypass antivirus. Any host block must be investigated as a release acceptance issue.
- The exact Windows x64 installer is validated here. Windows Arm64 is outside the current packaging target.
- Final acceptance on a fresh Windows 11 machine without Python, uv, WSL2, or developer tooling remains Packaging Phase 5.

## Final validation state

Packaging Phase 4 passed on Windows 11.

Observed final result:

- Packaging Phase 3 staging EXE build: PASS.
- Frozen worker smoke: PASS.
- Isolated lifecycle status smoke: PASS.
- Inno Setup 7 compilation: PASS.
- Isolated silent install: PASS.
- Installed `codemcp-remote.exe 0.1.0`: PASS.
- Installed lifecycle `status`: PASS (`status: stopped` in the isolated runtime root).
- Silent uninstall: PASS.
- Required-file removal verification: PASS.
- Installer Authenticode state: `NotSigned`.
- Bundled `tunnel-client`: `v0.0.12`, Apache-2.0.
- Bundled `tunnel-client.exe` SHA-256: `6649169733686805ca16cccd91774594d0c017fd729c37ad4ce1cd18323d9ae8`.
- Final installer SHA-256: `659651d9c0c1f333c39bf1ae4cee107c99bf147487f855aca4600309ec39c37c`.
- Final installer: `.local\installer-dist\codemcp-remote-setup.exe`.
- Checksum manifest: `.local\installer-dist\SHA256SUMS.txt`.
- Final smoke status: `passed`.

The installer smoke also confirmed cleanup of stale isolated smoke state without touching the active development lifecycle.

Packaging Phase 4 is closed. Do not enter Packaging Phase 5 without an explicit next-phase instruction.
