# Development State — codemcp-remote

Updated: 2026-08-30
Plan: `docs/plans/v0.1.0/open-source-readiness-plan.md`
Branch: `codex/open-source-readiness`
Session restore baseline HEAD: `a8fdb0139dc6ab21662ed41d8e72aa969b43f1a1`

## Current Phase

`v0.1.0` Open Source Readiness — **Final Release Gate IN PROGRESS**.

The Live Acceptance Ledger in the plan and the acceptance reports are authoritative for completed release evidence. Completed Phase/Stage gates must not be repeated unless a later code/artifact change invalidates their evidence.

## Completed

- Phase 6 / Stage 2 mandatory Windows real-host matrix: PASS / COMPLETE.
- Phase 7 functional F-01..F-20: PASS.
- Reliability R-01..R-14: PASS.
- Security acceptance: accounted for through S-33, with the documented Windows symlink privilege environment limitation.
- 10/10 real-project remote tasks: PASS / COMPLETE.
- Stage 6 secrets/dependency/license/supply-chain audit: PASS with documented `codemcp==0.3.0` license-metadata discrepancy.
- Hosted CI billing/spending-limit condition: WAIVED / ACCEPTED RISK; never record as CI PASS.
- Final automated source gate: PASS / COMPLETE.
- Documentation consistency: PASS.
- Acceptance-record synchronization completed on 2026-08-30 without rerunning completed phases: Phase 6 top-level/exit status, Phase 7 22-tool contract/F-01..F-20/R-01..R-14/ChatGPT-only/network-trust status, and the plan's Stage 2/3/4/5/7 summaries now match the Live Acceptance Ledger.
- The fourth audited RC production clean-machine `Prepare -> Start -> remote contract -> Cleanup` using disposable `phase5-clean` is historical PASS evidence; the old Phase H temporary-real-repository/Cleanup-deferred deviation is no longer a current packaging blocker.
- Draft release notes exist at `docs/releases/v0.1.0/release-notes.md`.
- Stage 5 GitHub Governance / CI: **PASS / COMPLETE with hosted CI WAIVED / ACCEPTED RISK**. Active ruleset `protect-master` protects the default branch, Dependabot hosted activation is proven for `uv` and `github-actions`, and template structure is verified.
- Final release-only source identity: **FROZEN by this checkpoint commit**. The exact immutable identity is the Git HEAD produced by this edit; all installer/ZIP builds and the `v0.1.0` tag must bind to that commit. Later evidence-only documentation must not redefine the release source identity.

## Remaining

1. Rebuild installer + ZIP from the frozen release-only source commit.
2. Recompute SHA-256, run artifact/security scan, and prove exact source/artifact identity.
3. Complete final clean-machine package / README onboarding / disposable-repo / cleanup / uninstall sign-off.
4. Bind final CHANGELOG / known limitations / release notes / checksum record to the frozen release commit and artifacts.
5. Final Release Gate sign-off.
6. Tag `v0.1.0` at the frozen release commit and publish the GitHub Release.

## Decisions

- Signing decision is **FINAL for `v0.1.0`: `NotSigned` / ACCEPTED LIMITATION**. No Authenticode certificate will be used; Windows SmartScreen / reputation / user-trust warnings are an explicitly accepted first-release limitation.
- GitHub hosted CI remains explicitly waived because billing blocked execution before runner/job start.
- Do not add required CI checks that cannot execute under the accepted hosted-CI waiver.
- `codemcp-remote-3243` is a separate parallel worktree/task and is out of scope for this release-gate session.

## Blockers

- No signing or GitHub-governance blocker remains. Public GitHub verification on 2026-08-30 proves `xqan/codemcp-remote` is public; active `protect-master` ruleset id `21844217` protects the default branch; Dependabot hosted activation is live for both `uv` and `github-actions`; PR template and Issue Form structure are verified.
- Hosted CI remains WAIVED / ACCEPTED RISK and the active ruleset intentionally has no required status-check rule.
- Remote `codex/open-source-readiness` is currently behind the local release-prep HEAD; final publication must push the frozen release commit before merge/tag.

## Tests

Revalidated on the restore baseline `a8fdb0139dc6ab21662ed41d8e72aa969b43f1a1`:

- Registered full test recheck: **PASS** — `353 passed, 7 skipped, 2 warnings` in 160.15s.
- Registered Ruff format check: **PASS** — `79 files already formatted`.
- Registered security audit: **PASS** for dependency audit, dependency-license evidence, current tracked-tree secret scan, and all-ref Git-history secret scan; `1339 commits scanned`, no leaks.
- Artifact scan: intentionally not rerun yet; final artifact must be rebuilt after the final release-only commit.
- A preceding full-test attempt hit transient Windows `WinError 32` while PyInstaller removed an old shared `.local/dist/codemcp-remote` tree. An immediate exact-HEAD recheck passed 353/7; no source change was made for that transient lock.
- Worktree remained clean after the revalidation commands.

## Next

1. Rebuild installer + ZIP from the frozen release-only source commit.
2. Revalidate SHA-256, artifact/security scan, and exact source/artifact identity.
3. Complete final clean-machine README/package/disposable-repo/cleanup/uninstall sign-off.
4. Bind final release notes/checksums, sign off the Final Release Gate, then tag/publish `v0.1.0`.
