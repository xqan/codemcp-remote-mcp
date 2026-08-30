# codemcp Pinned Baseline

> Updated: 2026-08-28  
> Status: **CURRENT DEPENDENCY BASELINE**

## 1. Pinned upstream

- Repository: `https://github.com/ezyang/codemcp`
- Runtime artifact: PyPI `codemcp==0.3.0`
- Compatibility source reference: release/tag `0.3.0`, commit `683e6ec29b15b91ec12430afabf5a45ed57d2489`
- Locked PyPI wheel SHA-256: `a56123f6e1544aed55dbfd1b4946fc2583222b4104a82d8a2171d8c1621cd32a`
- Locked PyPI sdist SHA-256: `a28161aa86176cebd1861e7c134ac98ab1762849d75b46915e0a9fc4ef6efae7`
- Distribution metadata `License` field: `MIT`
- Distribution `License-File`: Apache License 2.0 text

The `0.3.0` distribution contains an upstream licensing-metadata inconsistency: its package metadata says `MIT`, while the license file shipped in the same distribution is Apache-2.0. The release process therefore preserves the bundled Apache-2.0 license text verbatim, records both facts in `THIRD_PARTY_NOTICES.txt`, and keeps final license review open until the discrepancy is explicitly accepted or clarified.

codemcp-remote must not depend on the moving upstream default branch for the `v0.1.0` execution path.

The compatibility evidence is recorded in:

[`../reports/compatibility/codemcp-compatibility-matrix.md`](../reports/compatibility/codemcp-compatibility-matrix.md)

## 2. Current worker support

The current default worker is **Native Windows local**.

The Bridge uses a narrow compatibility entry point around the unchanged upstream `codemcp==0.3.0` package to handle the Windows subprocess/stdin and newline behavior required by Git-backed operations.

Current support matrix:

| Worker mode | `v0.1.0` role | Requirement |
|---|---|---|
| Native Windows local | **Default installed worker** | Git for Windows |
| WSL2 Ubuntu | Optional source-mode compatibility fallback | WSL2 + prepared worker environment |

Native Windows Git-backed mutation is supported and compatibility-tested.

WSL2 is no longer a mandatory installed-runtime dependency.

## 3. Intended use

codemcp is a downstream execution component for bounded operations such as:

- file reads/writes/edits;
- code search;
- formatting/test command integration;
- Git-related mutation behavior used behind Bridge policy.

codemcp is **not**:

- a reasoning engine;
- an autonomous agent;
- directly exposed to ChatGPT;
- allowed to bypass project/path/command policy;
- allowed to call a model provider.

The Bridge remains the public MCP server and security boundary.

## 4. Compatibility requirements

Any dependency upgrade must revalidate at least:

- MCP initialize and tool schema assumptions used by the adapter;
- file read/search/edit/write behavior;
- Native Windows subprocess behavior;
- WSL2 compatibility behavior when that fallback remains advertised;
- stdout/stderr encoding and bounding;
- timeout and process termination;
- Git commit/amend behavior;
- branch/HEAD stability checks;
- Bridge checkpoint/CAS assumptions;
- full codemcp compatibility matrix;
- Phase 6 lifecycle/reliability checks;
- Phase 7 security acceptance.

If an upstream change alters mutation, Git, subprocess or tool-contract semantics, the upgrade is rejected until an explicit design review updates the Bridge contract and tests.

## 5. License boundary

codemcp-remote project code is licensed `AGPL-3.0-only`.

The upstream `codemcp 0.3.0` distribution must be treated according to the artifact evidence above rather than a single inferred license label. Its bundled Apache-2.0 license text is preserved in the Windows release, while the conflicting `MIT` metadata value is recorded as an upstream discrepancy.

The Windows payload uses a generated `THIRD_PARTY_NOTICES.txt` plus per-component license files. A separate root `THIRD_PARTY_NOTICES.md` is not required for `v0.1.0`; the release gate must instead verify that the generated notice covers `codemcp`, `cloudflared`, and the optional OpenAI tunnel client and that the complete license files are present.
