# Git Policy

Phase 4 treats Git as a local protection and recovery boundary. The Bridge
does not expose arbitrary Git arguments and does not perform push, merge,
rebase, deploy, branch deletion, or force-reset operations on behalf of
ChatGPT.

## Mutation baseline

- A registered project must be the Git worktree root. This prevents a reset
  issued for one project from changing an unregistered sibling directory in a
  monorepo.
- The default policy requires an allowed branch and a clean worktree before
  every mutation.
- Before a mutation, the Bridge records branch, HEAD, changed paths, and a
  Git tree manifest containing object IDs. It also creates a ref in
  `refs/codemcp-remote/checkpoints/<checkpoint_id>`.
- After a successful mutation, the Bridge records the new branch/HEAD,
  changed paths, and a SHA-256 hash of the bounded diff representation.
- The SQLite row and audit events are linked to the operation. Source file
  contents are not copied into the database.

## Session WIP commits and checkpoint refs

The Bridge maintains two deliberately separate Git layers:

| Layer | Purpose | Lifetime and ownership |
| --- | --- | --- |
| Branch WIP commit | A clean, visible Git tip containing the latest session mutation | Created for the first eligible mutation; amended only with independently verified same-session evidence |
| Checkpoint ref | The exact pre-mutation commit used for diff, audit, and restore | Created and retained for every operation under `refs/codemcp-remote/checkpoints/<checkpoint_id>` |

The WIP commit footer is `Codemcp-Remote-Session: <session_id>`. A successful
amend requires the matching finalized SQLite mutation checkpoint, exact footer,
same branch and HEAD, clean worktree, and no other locally observable local
branch, remote-tracking ref, or tag containing the current tip. GitGuard repeats
the branch, HEAD, and shared-ref checks immediately before the amend. Missing,
malformed, or uncertain ownership evidence during mode selection falls back to
a new commit. A race discovered after a file side effect starts remains
`unknown` for reconciliation. Existing commits without the footer are not
automatically adopted.

This fallback is intentional: an amend can make the previous tip non-ancestral,
but the checkpoint ref created before that amend remains fixed and can still be
used for its operation's diff or restore. Checkpoint retention is unchanged;
there is no automatic ref cleanup or database migration in the session WIP
rollout.

For mutation finalization, the checkpoint audit diff compares that fixed ref
with the returned after-commit. GitGuard then performs a terminal HEAD/branch
read before SQLite finalization; a mismatch leaves the checkpoint unfinished and
the operation unknown.

This is a local observability guarantee, not a remote publication proof. A
remote may contain a commit before its tracking ref is updated locally. Active
session WIP commits must therefore not be manually pushed until the session's
mutations are complete.

## Manual checkpoint and diff

`checkpoint_create` is an approved mutation and only creates a lightweight
Bridge-owned ref after rechecking the clean worktree. `git_diff` accepts an
optional `checkpoint_id`; when present, it compares the current worktree with
that registered ref and rejects sensitive paths before returning any diff.

## Compare-and-swap rollback

`checkpoint_restore` requires the caller to obtain the current `head` from
`git_status` and pass it as `expected_head`. The Bridge then rechecks:

1. the checkpoint belongs to the current project and session;
2. its ref still resolves to the recorded commit;
3. the branch and HEAD still match the expected values; and
4. the worktree is still clean.

Only after a second explicit approval does the Bridge create a rollback safety
checkpoint and execute the fixed reset to the registered ref. A failed
compare-and-swap does not run Git reset. If the reset or post-reset check has
an uncertain result, the operation becomes `unknown` and must be reconciled.
