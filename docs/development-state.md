# Development State

Last updated: 2026-08-30

## Current branch and source state

- Branch: `codex/macos-cli-packaging`
- Source commit containing the BridgeError fix: `2ccfc5a5654b2c3077bedde0d498f4abebb27eff`
- macOS Release Candidate workflow is configured to run on pushes to `codex/macos-cli-packaging`.
- macOS candidate matrix includes `macos-arm64` and `macos-intel64`.

## macOS Intel64 live acceptance

Test host: Intel Mac (`x86_64`).
Registered project: `sample_project`.
Project root observed through codemacos: `/Users/qyf/projects/example-project`.

Verified PASS:

- Remote MCP connection to the Mac host.
- `project_open` succeeds on an allowed branch.
- Branch policy enforcement works: `main` was rejected with `BRANCH_NOT_ALLOWED`.
- Current accepted project branch: `develop`.
- `git_status` succeeds and reports a clean worktree.
- `file_list` succeeds.
- `file_read` succeeds.
- `file_create` succeeds.
- Mutations automatically create Git commits and Bridge-owned checkpoints.
- `git_diff(checkpoint)` returns the expected bounded diff.
- `file_delete` succeeds and commits the cleanup.
- Worktree returned to clean after the create/delete acceptance cycle.
- `checkpoint_create` correctly enters `awaiting_approval` and does not create a checkpoint before approval.
- Approval audit trail contains `operation.created -> validated -> approval.created -> awaiting_approval`.

Observed acceptance commits in `sample_project`:

- Baseline: `84aa5b9ca05d701d4dbc5fd935fc09ffef339356`
- Create acceptance file: `441cf87f45e3a761954ff81ff9a820e5d978915e`
- Cleanup acceptance file: `ceca4c30486cf30a33b39e8ac7e932bd55c8b817`

## Fixed macOS blocker

A packaged-runtime failure surfaced as:

`super(type, obj): obj must be an instance or subtype of type`

Root cause was `BridgeError` using zero-argument `super()` inside a `@dataclass(slots=True)` exception class.

Fix:

```python
Exception.__init__(self, self.message)
```

After the fix, the previous broad test failure collapsed from 42 failures to:

- 385 passed
- 2 failed
- 8 skipped

The remaining two failures are unrelated integration failures in the real codemcp read/edit path and are not the original BridgeError constructor failure.

## MCP host approval compatibility work

The blocking is now characterized as a ChatGPT MCP host-level issue rather than a macOS-specific Bridge failure:

- `approval_confirm` and `operation_cancel` were blocked before reaching `codemacos`.
- The same host-level blocking was later reproduced against the Windows development connector for `test_run` and `registered_command_run`.
- OpenAI MCP approval filtering consumes standard MCP `readOnlyHint` metadata, so the Bridge must publish accurate tool risk annotations instead of relying only on prose descriptions.
- Source now imports MCP `ToolAnnotations` and publishes closed-world risk hints for the high-risk execution/control surface:
  - destructive writes: `registered_command_run`, `format_run`, `test_run`, `checkpoint_restore`, `approval_confirm`, `operation_reconcile`
  - non-destructive state writes: `checkpoint_create`, `operation_cancel`
- Bridge-side policy, approval state, checkpoint, CAS, idempotency, and audit enforcement remain authoritative; the annotations are host-facing hints only.
- Contract coverage was added in `bridge/tests/test_mcp_tool_annotations.py`.
- Current source HEAD after the annotation implementation and contract-test correction: `dcbfad5bea3d2e24a46b2f06adb7e880b7013b92`.

Validation:

- Formatting check passed after the annotation implementation: 84 files already formatted.
- First full regression after adding annotations but before the contract test preserved the previous baseline: 385 passed, 2 failed, 8 skipped.
- The first run with the new contract test produced one new test-only failure because the current MCP Python model exposes annotation fields as snake_case; the test was corrected at `dcbfad5bea3d2e24a46b2f06adb7e880b7013b92`.
- A final full regression after that correction could not be started because ChatGPT blocked both `test_run` and the equivalent fixed `registered_command_run(test)` before either request reached the Bridge.
- The pre-existing two integration failures remain the known baseline until a final rerun can be completed.

## macOS Intel64 approval and restore acceptance

The annotated `macos-intel64` candidate was deployed and re-tested through the live `codemacos` connector. The previously observed host-level blocking is resolved for the approval/restore control path.

Verified PASS:

- `project_open` succeeded on `sample_project` / `develop`, clean at baseline HEAD `ceca4c30486cf30a33b39e8ac7e932bd55c8b817`.
- `checkpoint_create` operation `a1133255c6ec42728046a0e1acbd4555` entered `awaiting_approval` and created no checkpoint before confirmation.
- `approval_confirm` reached the Bridge and succeeded, creating manual checkpoint `3b02ac21903e4f82a2697bfd211beeed`.
- Replaying the same approval against the already-succeeded operation was rejected with `OPERATION_NOT_CANCELABLE`; the approval cannot execute the operation twice.
- A controlled mutation created `checkpoint-restore-acceptance.txt` and advanced HEAD to `f64bc1853464162ee74d96f9174f0ff13de4deef`.
- `checkpoint_restore` operation `68e59888488c4062ab9ca4516e52d5a9` correctly entered `awaiting_approval`.
- Confirming that restore succeeded and returned HEAD to `ceca4c30486cf30a33b39e8ac7e932bd55c8b817`.
- Restore automatically created rollback-safety checkpoint `31b6ca40996e49c4a9e4e471782b16fa`.
- Reusing stale `expected_head=f64bc1853464162ee74d96f9174f0ff13de4deef` after restore was rejected before approval with `CHECKPOINT_CONFLICT`; actual HEAD remained `ceca4c30486cf30a33b39e8ac7e932bd55c8b817`.
- The restore acceptance file is absent after rollback and the project is back at its pre-test Git state.

Conclusion: MCP `ToolAnnotations` deployment restored ChatGPT-host compatibility for `checkpoint_create`, `approval_confirm`, and `checkpoint_restore` without weakening Bridge-side approval, checkpoint, CAS, audit, or fail-closed behavior.

## macOS Intel64 development command acceptance

The registered development-command surface is now fully configured and accepted on the live Intel Mac:

- `sample_project` HEAD: `60b4180d99680615fcf0ea4cc68146911df20a0f`.
- Project-registry hot reload recognized runtime `projects.toml` changes without a Bridge restart.
- `project_status` reported `commands_resolved=true`.
- Available commands: `format`, `test`, `verify`.
- Command verification reported all three commands in `matched`, with no missing or mismatched commands.
- `codemcp_config_source=project`, `codemcp_config_ready=true`, `development_ready=true`, and `issues=[]`.
- `registered_command_run(verify)` succeeded through operation `660c5f59de774e7ab5e76d7c09d01441`.
- `test_run(test)` succeeded through operation `a6ab46a1c8d843cc91dd84a8071f436`.
- `format_run(format)` succeeded through operation `0f3ccffaf1244631ab620c18c47bb4f0`.
- Each command created a Bridge-owned mutation checkpoint with an empty diff hash and left branch `develop`, HEAD `60b4180d99680615fcf0ea4cc68146911df20a0f`, and the worktree clean.
- All three commands use the deterministic fixed argv `["/usr/bin/grep", "-qx", "test", "test.md"]`; no arbitrary shell, executable path, argv, or runtime parameter is model-controlled.

Conclusion: live Intel64 `registered_command_run`, `test_run`, and `format_run` acceptance is PASS, including project-registry hot reload and command-contract matching.

## Full repository regression

The final repository regression was re-run after the MCP annotation contract-test correction and after aligning the real Bridge integration fixture with the current release worker mode.

Result:

- 393 passed
- 0 failed
- 8 skipped
- 1 warning
- Runtime: 129.05 seconds
- Test operation: `79bba81b27374573be9f89bbb52bbbdb`

The two former failures in `bridge/tests/test_phase2_integration.py` were traced to a stale Phase 2 test assumption: the fixture still forced `worker_mode="wsl2"`, while the current release configuration uses `adapter_mode="native-stdio"` and `worker_mode="local"`.

The integration fixture now uses the release-default local/native worker path. WSL2-specific parameter and compatibility behavior remains covered by dedicated worker tests rather than making the primary release regression depend on the repository-local `.local/bridge-venv-wsl` environment.

Conclusion: repository regression gate PASS. The previous 2-failure baseline is closed and is not an accepted outstanding defect.

## macOS quarantine handling

The macOS installer now clears quarantine metadata from the extracted candidate directory before the rest of interactive setup runs:

- `scripts/codemcp-install.sh` preserves the existing POSIX `#!/bin/sh` contract.
- After resolving `SCRIPT_DIR`, Darwin runs `xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true`.
- Cleanup is recursive so the installer, executable, bundled Python extensions, and other extracted candidate files are covered.
- Cleanup is best-effort and non-fatal; non-macOS hosts do not execute it.
- Distribution checksum verification still runs immediately afterward. Removing the extended attribute does not modify packaged file bytes.
- `tests/integration/test_macos_executable_host.py` now requires the packaged `codemcp-install.sh` to contain the quarantine cleanup and verifies that it appears after `SCRIPT_DIR` resolution and before the executable/checksum setup path.

Validation after the installer change:

- Formatting gate PASS: 86 files already formatted.
- Full repository regression PASS: 393 passed, 0 failed, 8 skipped, 1 warning.
- Full regression operation: `9a54c6d919fb469e9ca0350ad9d3accb`.

This behavior is an unsigned-candidate usability measure. Re-evaluate whether it should remain once a Developer ID signed and notarized release path is available.

## macOS Intel64 final release gate

Final live Intel64 quarantine acceptance: PASS.

- The fresh `macos-intel64` candidate built from quarantine-cleanup source HEAD `ec05cb04efdd7d596e479ee4a87ac9c6a707dd49` was extracted and run on the Intel Mac.
- `./codemcp-install.sh` proceeded without requiring a manual `xattr -dr` step.
- No Gatekeeper/quarantine failure blocked the bundled executable or Python extension loading during this acceptance.
- The installer-owned `xattr -dr com.apple.quarantine "$SCRIPT_DIR"` behavior therefore passed its intended real-machine acceptance.
- Repository regression remains PASS at 393 passed, 0 failed, 8 skipped, 1 warning.

Conclusion: macOS Intel64 final release gate PASS for the unsigned candidate path.

## Implementation-plan synchronization

Both active implementation plans have been synchronized to the same repository facts:

- `docs/implementation-plan.md` is the English canonical implementation plan.
- `docs/zh-CN/implementation-plan.md` is an independent Chinese implementation plan, not a redirect-only mirror.
- Both record Intel64 live/final gate PASS, quarantine auto-cleanup PASS, MCP approval/restore/registered-command acceptance, the 393 passed / 0 failed / 8 skipped / 1 warning regression baseline, and the closure of the old `BridgeError` / WSL2 integration failure baselines.
- Both explicitly retain Apple Silicon real-host acceptance as the remaining architecture gate and keep the combined macOS / `v0.1.0` release blocked until that evidence and dependent final signoff are complete.
- Critical future status changes must be written to this development-state document, the English canonical plan, the independent Chinese plan, and the relevant acceptance ledger before the stage is considered complete.

## Current blocker

No Intel64 blocker remains. The remaining macOS dual-architecture release blocker is the real Apple Silicon / ARM64 final host acceptance and the documentation/release signoff that depends on it.

## Next steps

1. Push the documentation synchronization commits so the remote branch records the final Intel64 and plan state.
2. Keep ARM64 candidate parity under the same packaging contract and run the equivalent real-host acceptance on an Apple Silicon Mac.
3. Synchronize the ARM64 PASS/FAIL result back to `docs/development-state.md`, both implementation plans, and the macOS acceptance ledger.
4. Re-evaluate automatic quarantine removal if the project later adopts Developer ID signing and notarization.
