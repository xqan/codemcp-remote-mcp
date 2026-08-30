# codemcp-remote v0.1.0 — Release Notes

Status: **RELEASE BASELINE / ACCEPTANCE GATES CLOSED**

These notes describe the first stable public release baseline. Exact release commit and artifact SHA-256 values are intentionally not self-referentially embedded in this source file. The authoritative binding is carried by `BUILD_PROVENANCE.json`, `SOURCE_COMMIT.txt`, `SHA256SUMS.txt`, the immutable `v0.1.0` tag target, and the GitHub Release metadata/assets.

## Highlights

- Windows 11 packaged runtime with `codemcp-remote.exe`.
- Native Windows codemcp worker by default; WSL2 remains an optional source-mode compatibility fallback.
- Recommended public path: ChatGPT Connector (`Authentication = No authentication`) → OpenAI/ChatGPT Connector egress → Cloudflare WAF IP allowlist → Cloudflare Tunnel → loopback Bridge.
- Profile A reports `identity_level = network-only`; it is network trust, not ChatGPT user authentication.
- Optional OAuth Resource Server Profile B remains available for subject/client/scope-aware deployments.
- Exactly 22 public MCP tools; no arbitrary shell, caller-supplied argv, project administration, model provider, or hidden agent loop.
- Registered-project root confinement, sensitive-path denial, fixed command IDs, replay protection, Bridge-owned Git checkpoints, approval-gated restore, CAS rollback, persistent audit state, and explicit unknown-operation reconciliation.
- Local project add/remove is the administrative authorization control plane; the running Bridge observes validated registry changes without restart.

## Acceptance completed

- Phase 6 mandatory Windows real-host matrix: **PASS / COMPLETE**.
- Phase 7 functional matrix F-01 through F-20: **PASS**.
- Reliability R-01 through R-14: **PASS**.
- Security S-01 through S-33: accounted for; deterministic/live requirements are PASS except the separately documented Windows symlink privilege environment limitation.
- Real-project remote tasks: **10/10 PASS / COMPLETE** — Java 5/5, frontend 3/3, recovery 2/2.
- Final automated source gate: **PASS / COMPLETE** — standalone Ruff lint, Ruff format, configuration check, Python package build, `git diff --check`, tracked-diff verification, clean worktree/exact identity; final registered regression is `354 passed, 7 skipped, 2 warnings`, with `80 files already formatted`.
- Security audit: dependency audit, dependency-license evidence, current tracked-tree secret scan, and all-ref Git-history scan PASS. The codemcp 0.3.0 MIT/Apache metadata discrepancy remains explicitly documented and accepted by the engineering compatibility review.
- GitHub governance: **PASS / COMPLETE with hosted CI WAIVED / ACCEPTED RISK** — the public repository uses default branch `main` with active `protect-main` rules, PR-based protection, deletion/non-fast-forward protection, and no required hosted status checks.
- Network Trust Phase A–H: **PASS / COMPLETE** — ordinary public source traffic is blocked by Cloudflare policy, ChatGPT Connector source traffic is allowed, and Profile A remains explicitly `network-only`.
- Final hotfix clean-machine contract: **PASS** — packaged `APPROVAL_REQUIRED` handling, explicit `approval_confirm`, CAS checkpoint restore to the original baseline, clean worktree recovery, temporary mutation removal, and final Cleanup/uninstall all completed successfully.

## Known limitations and accepted risks

- Git for Windows is a required installed-runtime prerequisite.
- Profile A cannot identify a human user, ChatGPT account, Workspace, or conversation.
- This first release is single-operator local policy infrastructure, not a multi-user RBAC system.
- Automatic push, merge, rebase, deploy, arbitrary shell, arbitrary filesystem access, and model calls inside the Bridge are intentionally unavailable.
- `v0.1.0` is intentionally **`NotSigned`**. No Authenticode code-signing certificate is used for this release. Windows SmartScreen or reputation/user-trust warnings may appear; this is an explicitly accepted and published limitation.
- GitHub-hosted CI did not execute because of the recorded billing/spending-limit blocker. This is **WAIVED / ACCEPTED RISK**, not a CI PASS.
- `codemcp==0.3.0` has a documented upstream license metadata discrepancy: distribution metadata reports MIT while the bundled audited `License-File` is Apache-2.0. Both facts and the bundled license evidence are preserved.

## Release identity and publication binding

The release source is the final public `main` commit created from this release-only documentation freeze. After that immutable source commit is selected:

1. rebuild the installer and Windows ZIP from that exact public commit;
2. require `SOURCE_COMMIT.txt` and `BUILD_PROVENANCE.json` to report the exact tag target;
3. verify installer/ZIP SHA-256 and final artifact/security audit;
4. complete final clean-machine package identity, README onboarding, Start/health, Cleanup/uninstall sign-off;
5. publish tag `v0.1.0` and the GitHub Release with the installer, ZIP, checksum evidence, and these release notes.

Because the release commit and artifact hashes are outputs of the freeze/build process, they are recorded in release provenance and publication metadata rather than written back into this source file after the freeze.

## Documentation

- `README.md` — public onboarding and product boundaries.
- `docs/guides/windows-build-install-use.md` — Windows build/install/use flow.
- `docs/guides/cloudflare-tunnel-setup.md` — recommended Profile A network-trust deployment.
- `docs/acceptance/acceptance-test-plan.md` — Phase 7 acceptance record.
- `docs/plans/v0.1.0/open-source-readiness-plan.md` — release-gate requirements and source-freeze ledger.
- `docs/reports/testing/v0.1.0-dependency-license-compatibility-signoff.md` — dependency/license review.
