# Stage 6 Open-Source Security Validation

> Date: 2026-08-28
> Status: **STAGE 6 PASS WITH EXPLICIT HOSTED-CI WAIVER / FOURTH RC LOCAL SECURITY + PRODUCTION CLEAN-MACHINE REMOTE CONTRACT + MANUAL LICENSE SIGNOFF PASS**

## Scope

Stage 6 covers repository privacy, Git-history secret scanning, dependency vulnerability review,
third-party provenance/license handling, and final release-artifact secret/runtime-state scanning.

The release is not allowed to treat implementation of a scanner as equivalent to a passing scan.

## Repository-side controls implemented

### Current tracked tree privacy guard

`bridge/tests/test_open_source_privacy.py` now fails when current non-historical tracked content contains
known operator-specific deployment markers or when runtime/secret material becomes tracked.

The historical evidence boundary is explicit:

- `docs/releases/`
- `docs/reports/`

Historical evidence may retain redacted or factual acceptance records, but current source, scripts, tests,
guides, architecture, and plans must remain reusable and operator-neutral.

### Fixed local security-audit workflow

`scripts/validate-open-source-security.ps1` implements one fixed release-security workflow:

1. `uv audit --project bridge --frozen`;
2. export `HEAD` with `git archive`;
3. Gitleaks scan of the exported current tracked tree plus operator-specific deployment/path checks;
4. Gitleaks Git-history scan across all refs with `--log-opts=--all`;
5. optional directory/ZIP artifact scan;
6. explicit rejection of runtime/secret artifact names such as `projects.toml`, `remote.toml`,
   `*.dpapi`, `*.sqlite3*`, and `*.log`;
7. explicit rejection of operator-specific deployment/path data from final artifacts.

Reports are written only under ignored `.local/security-audit/`.

The `codemcp-remote` built-in project profile contains two fixed commands using this script:

- `security-audit`: source/dependency/current-tree/history gate;
- `artifact-audit`: the same gate plus mandatory scan of the standard
  `.local/release-candidate/codemcp-remote-v0.1.0-windows-x64.zip`.

Both commands are mirrored in root `codemcp.toml`. They do not accept arbitrary shell, executable paths,
or runtime argv through the Bridge.

### Gitleaks pin

Local Windows preparation is fixed to Gitleaks `8.30.0` Windows x64 with SHA-256:

`54fe94f644b832dd08e8c3a5915efb3bfa862386d59fb27ca0792cb687a83573`

Hosted Linux CI uses the same version with Linux x64 SHA-256:

`79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e`

The pin is intentional. Gitleaks `8.30.1` had a published Windows x64 checksum inconsistency, so the
release gate does not silently move to that asset. Any scanner upgrade requires an explicit checksum
and behavior review.

### Hosted CI gate

`.github/workflows/ci.yml` now has an `Open-source security gates` job that:

- checks out full Git history with credentials persistence disabled;
- runs locked `uv audit`;
- downloads and verifies pinned Gitleaks;
- scans a `git archive HEAD` current-tree export;
- scans full Git history.

A workflow file in the repository is not hosted-CI evidence. The first real hosted execution remains
part of the Stage 5/6 release evidence.

## Dependency provenance and license review

### codemcp 0.3.0

The execution dependency remains `codemcp==0.3.0` from PyPI, pinned by `bridge/uv.lock`.

Verified PyPI artifact hashes:

- wheel: `a56123f6e1544aed55dbfd1b4946fc2583222b4104a82d8a2171d8c1621cd32a`
- sdist: `a28161aa86176cebd1861e7c134ac98ab1762849d75b46915e0a9fc4ef6efae7`

The `0.3.0` distribution contains inconsistent license metadata:

- distribution `METADATA`: `License: MIT`;
- bundled `License-File`: full Apache License 2.0 text;
- the source reference used for compatibility review also identifies Apache-2.0.

The release therefore does not collapse this into an unsupported single-license claim. Packaging keeps
the bundled license text and records the metadata discrepancy in third-party notices.

### Windows build-tool provenance

The Windows EXE build no longer resolves PyInstaller from a floating transitive dependency graph.
`scripts/build-windows-exe.ps1` and `scripts/prepare-pypi-wheel.ps1` pin and SHA-256 verify the complete
PyInstaller build-tool wheel closure used by the `v0.1.0` Windows x64 path:

- `pyinstaller==6.22.2`;
- `pyinstaller-hooks-contrib==2026.6`;
- `altgraph==0.17.5`;
- `pefile==2024.8.26`;
- `pywin32-ctypes==0.2.3`;
- `packaging==26.3`;
- `setuptools==84.0.0`.

The helper verifies both the repository-pinned digest and the digest published in PyPI JSON before a
wheel can be consumed. Cached/downloaded bytes are then verified again.

The build extracts the verified PyInstaller wheel's upstream `COPYING.txt`, requires the bootloader
exception to be present, preserves it at `THIRD_PARTY/pyinstaller/COPYING.txt`, and writes a matching
`NOTICE.txt`. `BUILD_PROVENANCE.json` records the exact build-tool filenames, versions, and SHA-256
digests. The remote-transport staging step and installer smoke both fail closed if this evidence is missing.

### Bundled transport components

The Windows packaging path already preserves third-party provenance/license evidence for:

- `cloudflared`: pinned version and SHA-256, Apache-2.0 license;
- OpenAI `tunnel-client`: upstream checksum manifest, SPDX sidecar, license, archive/binary hashes;
- `codemcp`: pinned PyPI artifact hashes plus bundled license and discrepancy notice;
- PyInstaller bootloader/build output: verified wheel provenance plus upstream `COPYING.txt` and explicit
  `GPL-2.0-or-later WITH Bootloader-exception` notice.

The release-package contract uses generated `THIRD_PARTY_NOTICES.txt`, `BUILD_PROVENANCE.json`, and
component license files. A separate root `THIRD_PARTY_NOTICES.md` is not required for `v0.1.0`.

## Validation evidence

Before the Stage 6 security-workflow additions, the privacy/supply-chain remediation baseline passed:

- format: `76 files already formatted`;
- tests: `335 passed, 7 skipped, 0 failed`.

The first audited RC completed the local release-security workflow successfully:

- format: **`77 files already formatted`**;
- full regression: **`342 passed, 7 skipped, 0 failed`**;
- warnings: **`2`** non-blocking warnings;
- `uv audit`: **PASS** — `47 packages`, `0 known vulnerabilities`, `0 adverse project statuses`;
- current tracked-tree Gitleaks: **PASS** — no findings;
- full Git-history Gitleaks: **PASS** — no findings;
- final RC artifact Gitleaks: **PASS** — no findings;
- dependency-license inventory: **47/47 installed locked packages accounted for**, `missing_license_evidence=[]`;
- installer SHA-256: `902e15205aee3d585fafa0248c89419171f5a14d6bb249655820318d4fd8e7c6`;
- RC ZIP SHA-256: `0ce11c91e5735808ffa1260755ba4030adbed220f5d2f9fe5ca9818b3a39fed1`;
- staging payload audit: **PASS**;
- final RC audit: **PASS**.

That RC then reached the clean-machine remote contract. `Prepare` and `Start` passed, the Bridge and Tunnel
were healthy, `phase5-clean` opened through the remote connector, read-only operations passed, and a remote
`file_create` mutation committed successfully. The immediately following same-session `file_delete` exposed
a deterministic Git finalization bug: the Bridge selected `AMEND_SESSION_WIP`, the create-then-delete net
effect was empty, and `git commit --amend --no-edit --only -- <path>` rejected the empty amend. The Bridge
correctly surfaced `UNKNOWN_SIDE_EFFECT` and left the disposable worktree blocked instead of reporting false
success.

The source fix adds `--allow-empty` only to the already-proven `AMEND_SESSION_WIP` commit mode. This preserves
the one-WIP-commit-per-session model while allowing a same-session create/delete sequence to converge to an
empty but valid session-owned WIP commit. A regression test reproducing the exact remote sequence now passes:

- format after the fix: **`77 files already formatted`**;
- full regression after the fix: **`343 passed, 7 skipped, 0 failed`**;
- warnings: **`2`** non-blocking warnings.

The source fix was carried into a second audited RC:

- installer SHA-256: `7416b0d78bb07213015153bff892205d65db36d2c0a48dbdde06c520eefd0cc6`;
- RC ZIP SHA-256: `d55b429c52b63ceb6b52c5bafcd7b2d00c48a6855957af5f70875aea8baa1c2e`;
- staging payload audit: **PASS**;
- final RC audit: **PASS**;
- managed clean-machine upgrade from the first RC: **PASS**;
- upgraded `Prepare` and `Start`: **PASS**, with Bridge and Tunnel healthy.

The upgraded remote contract then exposed a second, narrower harness-policy mismatch before mutation testing:
`phase5-clean` was still created with `git init --initial-branch=main`, while the current production default
branch policy permits `develop`, `develop/*`, `codex/*`, and `feature/*`. Remote `project_open` therefore
correctly failed closed with `BRANCH_NOT_ALLOWED`. The production branch policy was not weakened. Instead,
the disposable clean-machine acceptance repository now initializes on `develop`, and a regression locks out
a return to `main`.

After the harness branch fix:

- format: **`77 files already formatted`**;
- full regression: **`343 passed, 7 skipped, 0 failed`**;
- warnings: **`2`** non-blocking warnings.

The branch fix was carried into a third audited RC:

- installer SHA-256: `ad995d6a9042635f601d90dc72cf36c3b56ec357d3bbcd7e5206e70df74f82a0`;
- RC ZIP SHA-256: `14a80753823a5d717aec544435ca975932528406b133c79689f026b633118505`;
- staging payload audit: **PASS**;
- final RC audit: **PASS**;
- managed clean-machine upgrade from the second RC: **PASS**;
- upgraded `Prepare` and `Start`: **PASS**;
- remote `project_open`: **PASS** on `develop`, with the exact recorded clean baseline.

Before the first new mutation, the Bridge correctly returned `OPERATION_BLOCKED` because the first RC's
persisted `unknown` mutation still owned the project lock. That exposed a third recovery defect: graceful
Bridge shutdown closes the origin session with reason `bridge_shutdown`, while successor reconciliation had
only accepted origins blocked by `bridge_restart`. The unknown mutation therefore survived the upgrade but
could not be inspected or reconciled by a new same-security-context session.

The recovery fix keeps the ownership boundary intact and permits successor access only for `unknown`
operations in the same project, with the same owner and matching persisted authentication context, when the
origin was either `blocked/bridge_restart` or `closed/bridge_shutdown`. `operation_status` now uses the same
authorization boundary so an authorized successor can inspect checkpoint/audit evidence before reconciling.
The exact graceful-shutdown recovery regression now passes:

- format: **`77 files already formatted`**;
- full regression: **`344 passed, 7 skipped, 0 failed`**;
- warnings: **`2`** non-blocking warnings.

The recovery fix was carried into the fourth audited RC:

- installer SHA-256: `3491844237e28cc6b1532b5b85dbf2a5920badddb81a9ff7fb3dfb4d8ac93b50`;
- RC ZIP SHA-256: `498e11aade3da2cfd47c2b28d2086dd657ee8bd325f199ad59037813aeb07d2c`;
- staging payload audit: **PASS**;
- final RC audit: **PASS**;
- managed upgrade from the third RC: **PASS**;
- successor `operation_status` for the historical `unknown`: **PASS**, proving the new same-auth-context recovery visibility;
- historical successful reconciliation could not be completed because an earlier acceptance `Prepare` had already
  deleted and recreated the disposable Git repository, removing the old checkpoint ref; the Bridge correctly
  rejected the attempt with `CHECKPOINT_INVALID` rather than weakening checkpoint verification.

After `Cleanup` + `Reset`, the same fourth RC was installed again as a truly fresh clean-machine baseline and
completed the full remote mutation contract:

- fresh `Prepare`: **PASS**, `previous_installer_sha256=null`;
- fresh `Start`: **PASS**, Bridge/Tunnel health both `ok`;
- `project_open`: **PASS** on `develop`, exact baseline
  `f442d3f56ffd9ec485c8e34de495c8974be5e18c`, clean worktree;
- baseline file discovery/read: **PASS**, `PHASE5_ACCEPTANCE.txt=phase5-clean-machine`;
- same-session `file_create`: **PASS**, operation `c7ed4d822942483c8f14031db595aa7e`;
- created marker read-back: **PASS**;
- same-session `file_delete`: **PASS**, operation `385715677e094f2d9ccbeff5f0237c29`;
- deleted marker re-read: correctly failed `FILE_NOT_FOUND`;
- final Git status: **PASS**, `develop`, head
  `21082d46a529332e0e663bce639df8b8317276bf`, `dirty=false`, no changed files;
- create/delete audit context remained `network-trusted`, `network-only`,
  issuer `network-trust://cloudflare-chatgpt`, principal/resource
  `network-chatgpt-v1` / `https://codemcp2.quickclip.cc/mcp`.

The dependency-license inventory still reports `manual_compatibility_review_required=true` as an automated
guard, but the exact fourth-RC engineering compatibility review has now been completed and recorded in
`docs/reports/testing/v0.1.0-dependency-license-compatibility-signoff.md` as **PASS WITH DOCUMENTED DISCREPANCY**.
The signoff preserves the `codemcp 0.3.0` MIT/Apache metadata discrepancy and does not represent legal advice.

## Stage 6 closure

The fourth RC has passed the production clean-machine remote mutation contract. All mandatory local, artifact, clean-machine, and license-review gates are complete; hosted CI is explicitly waived as an accepted release risk because GitHub blocked every job before runner start for billing/spending-limit reasons.

- [x] reproduce the clean-machine remote mutation failure with auditable `UNKNOWN_SIDE_EFFECT` evidence;
- [x] fix the same-session create/delete empty-amend bug without weakening branch/worktree/CAS checks;
- [x] reproduce the clean-machine `BRANCH_NOT_ALLOWED` harness mismatch after the rebuilt RC upgrade;
- [x] align the disposable acceptance repository with the default allowed `develop` branch;
- [x] reproduce the graceful-shutdown successor recovery lock for a persisted `unknown` mutation;
- [x] allow only same-project/same-owner/same-auth-context successor inspection and reconcile after `bridge_shutdown`;
- [x] full regression PASS after all fixes (`344 passed, 7 skipped`);
- [x] rebuild the installer and RC ZIP from the latest source;
- [x] rerun dependency, tracked-tree, full-history, staging-payload, and final-RC audits against the fourth RC;
- [x] rerun the production clean-machine remote contract through `project_open`, read, create/delete mutation, and final clean Git status;
- [x] clean-machine `Cleanup` after the passing fourth-RC remote contract: **PASS**, Bridge/Tunnel stopped and installer removed while preserving runtime evidence;
- [x] manual dependency/license compatibility review against the exact fourth-RC lockfile and payload: **PASS WITH DOCUMENTED DISCREPANCY**;
- [x] hosted CI security job — **WAIVED / ACCEPTED RISK**: attempted, but GitHub blocked every job before runner start because of account billing/spending-limit state. This is not recorded as CI PASS and is not treated as a code/security-gate failure.

## Execution boundary

The previous RC remains useful historical evidence because its local automated security gates passed and its
clean-machine run discovered the release-blocking remote mutation defect. It is superseded by the source fix
and must not be published as the final `v0.1.0` artifact.

The fourth RC has completed the required clean-machine `Prepare` → `Start` → remote connector contract →
`Cleanup` sequence. No further clean-machine execution is required for Stage 6 unless the release artifact
changes.
