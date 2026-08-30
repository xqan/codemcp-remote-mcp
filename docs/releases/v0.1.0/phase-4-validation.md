# Phase 4 Validation

## Scope

Phase 4 adds Bridge-owned Git checkpoints, bounded checkpoint diffs, baseline
metadata for mutation operations, compare-and-swap rollback, and the follow-up
session WIP commit safety behavior. Secure MCP Tunnel integration remains
deferred to Phase 5.

## Implemented surface

- `checkpoint_create`: clean-worktree checkpoint creation with explicit
  approval.
- `checkpoint_restore`: two-step approved restore of a registered checkpoint
  with caller-supplied expected HEAD.
- `git_diff(checkpoint_id=...)`: bounded, sensitive-path-filtered comparison
  against a registered checkpoint.
- Automatic mutation checkpoints for `file_edit`, `format_run`, `test_run`,
  and rollback safety checkpoints.
- Session WIP commits use an exact `Codemcp-Remote-Session` footer and amend
  only after SQLite, checkpoint, branch, HEAD, clean-worktree, and locally
  observable shared-ref evidence agrees; GitGuard repeats the checks before the
  amend and finalization uses an expected after-HEAD/branch CAS.
- Mutation checkpoint audit diffs compare the fixed checkpoint ref with the
  returned after-commit, followed by a terminal HEAD/branch CAS before SQLite
  finalization.
- SQLite migration 3 with checkpoint metadata and audit linkage.

## Validation commands

~~~text
uv sync --project bridge
uv run --project bridge codemcp-bridge-server check
uv run --project bridge ruff check bridge/src bridge/tests
uv run --project bridge pytest -q --basetemp=.local/pytest-phase4
~~~

The Phase 4 and follow-up tests cover a Chinese/space-containing project path,
clean and dirty worktrees, before/after HEAD and tree metadata, sensitive diff
rejection, manual checkpoint approval, checkpoint diff, external HEAD races,
CAS rejection, safety checkpoint creation, successful restore, session WIP
amend, restart/reconcile, successor-session isolation, idempotent replay, and
unknown/cancelled mutation handling.

## Phase 3 final acceptance record

The final validation baseline is commit `c442e4d` on 2026-08-24. The focused
acceptance matrix passed `13` tests, covering all six file mutations, session and
restart isolation, locally observable shared-ref cut-off, both external-HEAD
CAS windows, checkpoint restore/reconcile, and `UNKNOWN_SIDE_EFFECT` handling.

The complete validation results were:

- Bridge: `122 passed, 3 skipped, 6 failed`.
- Integration: `4 passed, 2 xfailed`.
- `codemcp-bridge-server check`: `status=ok`, phase 5, three registered projects,
  and `model_egress=deny`.
- Python `compileall`: passed.
- Full Ruff: 12 findings, all pre-existing lint debt; no new finding was
  introduced by the Phase 2.5/2.5.1 changes.

The six Bridge failures are classified as baseline exceptions rather than
regressions in this feature:

1. `test_existing_symlink_config_is_rejected` cannot create a symlink under the
   current Windows account (WinError 1314); the same environment skips other
   symlink tests.
2. `test_root_only_maven_profile_runs_doctor_compile_and_test` reports a failed
   WSL2/worker operation instead of success.
3. `test_real_codemcp_bridge_read_edit_command_and_diff` reports the same
   WSL2/worker integration failure.
4. `test_real_codemcp_bridge_worker_restarts_after_bridge_shutdown` reports the
   same WSL2/worker integration failure.
5. `test_local_mcp_contract_and_policy_rejections` compares an LF fixture hash
   with a CRLF file produced by the Windows text path.
6. `test_file_write_requires_matching_sha256` has the same CRLF/LF fixture hash
   mismatch.

The 12 Ruff findings are also pre-existing and are recorded individually here
so they are not mistaken for Phase 3 regressions:

| Location | Rule | Classification |
| --- | --- | --- |
| `bridge/src/codemcp_bridge/command_runner.py:159` | E501 | Existing long line |
| `bridge/src/codemcp_bridge/generated_codemcp.py:13` | UP035 | Existing `typing.Iterator` import style |
| `bridge/src/codemcp_bridge/mcp_server.py:580` | F841 | Existing unused `file_size` |
| `bridge/src/codemcp_bridge/mcp_server.py:2093` | E501 | Existing long line |
| `bridge/src/codemcp_bridge/project_detection.py:9` | UP035 | Existing `typing.Mapping` import style |
| `bridge/src/codemcp_bridge/project_profiles.py:7` | UP035 | Existing `typing.Mapping` import style |
| `bridge/src/codemcp_bridge/security_defaults.py:6` | UP035 | Existing `typing.Mapping` import style |
| `bridge/tests/test_generated_codemcp_operation.py:222` | E501 | Existing long test line |
| `bridge/tests/test_phase2_worker.py:160` | UP037 | Existing quoted annotation |
| `bridge/tests/test_phase2_worker.py:240` | UP037 | Existing quoted annotation |
| `bridge/tests/test_phase2_worker.py:299` | UP037 | Existing quoted annotation |
| `bridge/tests/test_phase2_worker.py:363` | UP037 | Existing quoted annotation |

These exceptions are outside the Phase 3 mutation/checkpoint paths and remain
follow-up environment or lint-debt items. The functional Phase 3 acceptance
criteria are therefore closed with the documented baseline exceptions.

## Known limitations

- The current compatibility decision still requires WSL2 for codemcp
  Git-backed mutation; native Windows codemcp mutation remains unsupported.
- Diff hashes are computed over the bounded diff returned by GitGuard. The
  full diff is never persisted.
- A checkpoint ref is retained until a future retention policy is designed;
  Phase 4 and the session WIP follow-up do not delete user branches or clean up
  checkpoint refs.
- Secure MCP Tunnel is not connected.

## Session WIP rollout constraints

The rollout is backward-compatible with existing SQLite data and MCP request
parameters. Historical commits or checkpoints without the new footer are
treated as lacking ownership evidence and cause a safe CREATE fallback. A code
rollback requires no database downgrade and does not delete either old or new
checkpoint refs. Operators should not publish an active session's WIP before
the session's mutations are complete; a local remote-tracking ref may otherwise
force the next mutation to create a new commit.

The shared-ref check is limited to local branch, tag, and remote-tracking refs.
It cannot prove that an unseen remote ref does not already contain the WIP.
