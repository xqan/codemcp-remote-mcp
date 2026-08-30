# Open Source Readiness Baseline

> Stage: 0 — Release Freeze and Baseline
> Date: 2026-08-24
> Baseline commit at freeze start: `47c7ecc457d1dcc79078aa8db46ade2d9ff18c27`
> Working branch: `codex/20260824`

## Frozen support scope

- Host: Windows 11.
- Mutation worker: WSL2 Ubuntu.
- Python: 3.12+.
- Execution backend: `codemcp==0.3.0`.
- Remote transport: OpenAI Secure MCP Tunnel.
- Authorization model: single-user local policy profile.
- Reasoning boundary: ChatGPT is the only reasoning engine; Bridge and codemcp do not host an agent loop or model provider.
- Native Windows Git-backed mutation remains unsupported for `v0.1.0`.

## Open-source license decision

The project code is intended to be released under GNU Affero General Public License v3.0 only (`AGPL-3.0-only`).

`codemcp==0.3.0` remains a third-party Apache-2.0 dependency. Its upstream license is not changed by this project.

## Stage 0 verification status

| Check | Status | Evidence / note |
|---|---|---|
| Support scope frozen | PASS | Recorded above and aligned with the current implementation plan. |
| Branch / HEAD recorded | PASS | Freeze-start baseline recorded above. |
| Working tree clean | PASS | Bridge project status reported clean before the Stage 0 license edits. |
| Existing full test suite | BLOCKED | The registered `test` command was resolved successfully, but remote invocation was blocked by the platform safety layer before execution. This is not a test failure. |
| `git diff --check` | PENDING | No dedicated registered command currently exposes this check. |
| Unrecorded blockers | PASS | The two unresolved verification items above are explicitly recorded. |

## Stage 0 exit rule

Stage 0 MUST NOT be marked complete until:

1. the full registered test suite is executed and passes;
2. `git diff --check` is executed and passes;
3. the resulting release-readiness commit is clean;
4. any failure found by those checks is either fixed or promoted to an explicit blocker.

Until then, later non-conflicting documentation and license work may proceed, but `v0.1.0` remains blocked.
