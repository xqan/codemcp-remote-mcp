# Stage 5 Validation — GitHub Governance and CI

Date: 2026-08-24

## Status

Repository-side implementation: **COMPLETE**

GitHub-hosted CI execution: **WAIVED / ACCEPTED RISK** — the hosted run was blocked by the recorded billing/spending-limit condition before runner/job execution and is not counted as PASS.

Hosted governance activation: **PASS / COMPLETE** — the repository is public, Dependabot hosted activation is proven for both `uv` and `github-actions`, the PR template is recognized on the default branch, both Issue Form YAML files are present and schema-valid, and the default branch is protected by an active GitHub ruleset.

Public GitHub evidence captured and reverified on 2026-08-30:
- repository `xqan/codemcp-remote`: `visibility=public`, default branch `master`;
- active ruleset `protect-master` (id `21844217`), target `branch`, condition `~DEFAULT_BRANCH`;
- `master`: `protected=true`;
- ruleset requires pull-request-based changes with `required_approving_review_count=0`;
- ruleset restricts branch deletion and blocks non-fast-forward / force-push updates;
- no required status-check rule is configured, preserving the hosted-CI waiver;
- Dependabot dynamic workflows: active;
- generated Dependabot branches exist for both `github_actions` and `uv`, with open Dependabot PRs proving hosted scheduling/activation;
- GitHub Community Profile recognizes `.github/pull_request_template.md`;
- `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` are present on `master` and validate as GitHub Issue Form syntax.

The interactive New Issue form itself could not be inspected from the unauthenticated verification client, so this record does not claim visual rendering evidence beyond the default-branch files/schema validation. The hosted CI waiver does not convert an unexecuted Actions job into PASS.

## Delivered

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `CHANGELOG.md`
- `.github/workflows/ci.yml`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/pull_request_template.md`
- `.github/dependabot.yml`
- README links to the governance documents

The CI workflow uses read-only repository permissions, disables persisted checkout credentials, runs on both Ubuntu and Windows for the Python 3.12 core checks, validates the locked environment, Ruff lint/format, pytest, example configuration, package build, and Git whitespace/cleanliness.

GitHub Actions dependencies are pinned to an exact immutable release or commit. Dependabot monitors both `/bridge` uv dependencies and GitHub Actions weekly.

## Local validation evidence

### Ruff format

Command: registered project workflow `format`

Result: **PASS**

```text
49 files already formatted
```

The first format run exposed two pre-existing formatting defects in `bridge/tests/test_phase2_integration.py` and `bridge/tests/test_phase2_worker.py`. Only Ruff-equivalent whitespace/layout changes were applied, then the check passed.

### Tests

Command: registered project workflow `test`

Result: **PASS**

```text
127 passed, 390 warnings in 56.00s
```

Warnings are not treated as hidden success criteria. The observed warnings include Python 3.14 asyncio deprecations, a Pydantic incomplete forward-reference warning, and a pytest cache permission warning. They remain visible technical debt for later release review.

### Git state

Result: **PASS**

- branch: `codex/20260824`
- working tree: clean

## Hosted governance final record

1. hosted CI remains **WAIVED / ACCEPTED RISK** for `v0.1.0`; the `CI` workflow is currently disabled and no CI check is required by the release ruleset;
2. **PASS** — Dependabot hosted activation is proven for both `uv` and `github-actions` by active Dependabot workflows plus generated branches/PRs;
3. **PASS** — `master` is protected by active ruleset `protect-master` (id `21844217`), which requires pull-request-based changes, restricts deletion, and blocks non-fast-forward / force-push updates;
4. **PASS / STRUCTURAL HOSTED EVIDENCE** — the PR template is recognized by GitHub Community Profile; both Issue Form files are present on the default branch and schema-valid. Interactive visual rendering remains a manual authenticated UI spot-check, not a repository-content blocker;
5. **PASS** — the active ruleset contains no required status-check rule, so it does not conflict with the hosted-CI waiver.

## Conclusion

Stage 5 GitHub Governance / CI is **PASS / COMPLETE with hosted CI WAIVED / ACCEPTED RISK**. Public GitHub verification proves repository visibility, default-branch governance files, active Dependabot for both configured ecosystems, PR-template recognition, and active `master` protection through `protect-master`. Hosted CI remains explicitly waived and is not counted as PASS. No Stage 5 blocker remains.
