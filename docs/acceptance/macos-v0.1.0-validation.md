# macOS v0.1.0 Validation Ledger

> Status: **PHASE 3 GITHUB-HOSTED NATIVE GATE PASS / PHASE 4 CLEAN-HOST PENDING**
>
> Authoritative build workflow: `.github/workflows/macos-release.yml`
>
> Release policy: ad-hoc signing only; no Developer ID certificate; no notarization.

## Evidence model

The macOS release candidates are authoritative only when both native GitHub-hosted jobs and the convergence job complete from the same source commit:

| Candidate | GitHub runner | Native arch | Expected file | Status |
| --- | --- | --- | --- | --- |
| Apple Silicon | `macos-15` | `arm64` | `codemcp-remote-v0.1.0-macos-arm64.tar.gz` | PASS |
| Intel | `macos-15-intel` | `x86_64` | `codemcp-remote-v0.1.0-macos-intel64.tar.gz` | PASS |
| Convergence | `ubuntu-latest` | n/a | `macos-v0.1.0-convergence.json` | PASS |

A local Mac build is smoke evidence only. It does not replace the corresponding GitHub-hosted authoritative build.

## Required per-architecture evidence

Each native build job must preserve:

- source commit and release version;
- runner OS, machine architecture, macOS version and build;
- outer archive SHA-256 and `*.tar.gz.sha256`;
- internal `SHA256SUMS.txt` verification;
- `BUILD_PROVENANCE.json` using `codemcp-remote-build-provenance-v2`;
- thin Mach-O architecture checks for every bundled Mach-O;
- strict ad-hoc `codesign` verification with no project Developer ID team identity;
- no Homebrew or user-workspace absolute Mach-O dependency path;
- bundled `cloudflared 2026.7.3` smoke;
- frozen executable host smoke;
- simulated quarantine `spctl` rejection evidence, recorded as the expected limitation of an ad-hoc, non-notarized build;
- validation evidence using `codemcp-remote-macos-validation-v1`.

No signing certificate, notary credential, Cloudflare token, API key, Keychain database, or runtime configuration is allowed in workflow inputs, logs, or uploaded artifacts.

## Convergence evidence

The convergence job must reject the run unless both candidates agree on:

- source commit;
- release version;
- provenance schema;
- source metadata other than target architecture;
- pinned Python/uv/PyInstaller tool versions recorded by provenance;
- `bridge/uv.lock` SHA-256;
- signing mode (`adhoc`, `developer_id=false`);
- notarization state (`not_performed`, reason `no_certificate`).

The two candidates must differ in exactly the expected architecture/file identity: `arm64` / `macos-arm64` and `x86_64` / `macos-intel64`.

## Phase 3 run record

A GitHub-hosted native dual-architecture run has completed successfully. The exact run ID, attempt number, archive SHA-256 values, and convergence JSON remain stored in the GitHub Actions run artifacts and have not yet been transcribed into this repository ledger.

- Source commit: `62a7efb55decf7909a994cdb3998691433cace1b`
- GitHub Actions run ID: NOT YET TRANSCRIBED
- Run attempt: NOT YET TRANSCRIBED
- arm64 archive SHA-256: NOT YET TRANSCRIBED
- Intel archive SHA-256: NOT YET TRANSCRIBED
- Convergence evidence: workflow job PASS; exact JSON not yet transcribed
- Phase 3 Gate: **PASS**

This PASS is based on the successful GitHub-hosted `macos-15` arm64 build, `macos-15-intel` x86_64 build, and convergence job from the same source commit. Missing transcription fields must be copied from the retained workflow artifacts before final release signoff.

## Phase 4 clean-machine record

Phase 4 remains separate from CI build evidence.

- Real Intel Mac clean-host acceptance: PENDING
- Real Apple Silicon Mac clean-host acceptance: PENDING
- Minimum supported macOS version claim: PENDING
- Keychain upgrade/relocation/locked-denied matrix: PENDING
- 20/20 lifecycle matrix per architecture: PENDING
- Quarantine/manual-release user path: PENDING
- Final `v0.1.0` release gate: **BLOCKED**
