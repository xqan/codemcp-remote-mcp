# macOS v0.1.0 Build, Install, and Clean-Host Validation

Phase 3 GitHub Actions is the authoritative source for the native arm64 and x86_64 candidates. Local Mac builds are smoke-only. Phase 4 real-host evidence is required before macOS is declared supported.

## Intel Phase 4

Download the successful `macos-candidate-macos-intel64` artifact from the converged GitHub Actions run and extract its ZIP. Keep the tar.gz, its `.sha256` sidecar, and `macos-intel64-validation.json` together.

Run:

```bash
bash ./scripts/validate-clean-macos-release.sh --action prepare --archive "/path/to/codemcp-remote-v0.1.0-macos-intel64.tar.gz" --validation-json "/path/to/macos-intel64-validation.json"
```

The harness validates archive identity, native architecture, internal checksums, ad-hoc signing, provenance, and expected Gatekeeper rejection. It creates an isolated logical HOME at `~/codemcp-remote-phase4/user-home` and a disposable Git project.

After successful verification, explicitly release quarantine:

```bash
xattr -dr com.apple.quarantine "$HOME/codemcp-remote-phase4/distribution/codemcp-remote"
```

Then run the exact interactive installer command printed by the harness. Enter the Cloudflare token only at the hidden TTY prompt. Register project ID `phase4-macos` using the disposable project path printed by the harness.

After installation run, in order:

```bash
bash ./scripts/validate-clean-macos-release.sh --action verify
bash ./scripts/validate-clean-macos-release.sh --action secret-scan
bash ./scripts/validate-clean-macos-release.sh --action lifecycle --cycles 20
```

`secret-scan` asks for the token again with terminal echo disabled and feeds it to `grep` through stdin, never argv or a temporary file. `lifecycle` requires 20 clean start/status/stop cycles and rejects process residue.

Preserve `~/codemcp-remote-phase4/evidence.json` before cleanup. Then:

```bash
bash ./scripts/validate-clean-macos-release.sh --action cleanup
```

Cleanup stops owned services, deletes only the Keychain account derived for the isolated acceptance HOME, and removes only the harness-owned sandbox.

## Apple Silicon

Repeat the same procedure on a real Apple Silicon Mac using `macos-candidate-macos-arm64`. Intel PASS does not substitute for Apple Silicon clean-host evidence.

## Release limitation

The macOS v0.1.0 candidates are ad-hoc signed and not notarized. Gatekeeper rejection on quarantined binaries is expected. Do not describe the release as Developer-ID signed, notarized, or Apple-trusted.
