"""Bridge-owned Git checkpoints and compare-and-swap restore operations."""

from __future__ import annotations

import logging
import string
import uuid
from typing import Any

from .db import CheckpointRecord, Database
from .errors import BridgeError
from .git_guard import CommitMode, GitGuard
from .settings import ProjectSpec

_LOGGER = logging.getLogger(__name__)


class CheckpointService:
    """Persist checkpoints while keeping all Git mutations narrowly scoped."""

    def __init__(self, database: Database, git: GitGuard):
        self._database = database
        self._git = git

    @staticmethod
    def validate_expected_head(head: str) -> None:
        if (
            not isinstance(head, str)
            or len(head) not in {40, 64}
            or any(character not in string.hexdigits for character in head)
        ):
            raise BridgeError(
                "INVALID_REQUEST",
                "expected_head must be a Git commit hash",
                {"field": "expected_head"},
            )

    @staticmethod
    def _ref_name(checkpoint_id: str) -> str:
        return f"refs/codemcp-remote/checkpoints/{checkpoint_id}"

    async def create(
        self,
        project: ProjectSpec,
        *,
        session_id: str | None,
        operation_id: str | None,
        kind: str,
    ) -> CheckpointRecord:
        if kind not in {"manual", "mutation", "rollback_safety"}:
            raise BridgeError("INVALID_REQUEST", "invalid checkpoint kind")
        await self._git.require_worktree_root(project.root)
        snapshot = await self._git.snapshot(project.root)
        if snapshot.dirty:
            raise BridgeError(
                "WORKSPACE_DIRTY",
                "checkpoint requires a clean workspace",
                {"changed_files": list(snapshot.changed_files)},
            )
        checkpoint_id = uuid.uuid4().hex
        ref_name = self._ref_name(checkpoint_id)
        await self._git.create_checkpoint_ref(project.root, ref_name, snapshot.head)
        try:
            return self._database.create_checkpoint(
                checkpoint_id=checkpoint_id,
                project_id=project.project_id,
                session_id=session_id,
                operation_id=operation_id,
                owner_id="local-policy",
                kind=kind,
                branch=snapshot.branch,
                head=snapshot.head,
                ref_name=ref_name,
                before_data=snapshot.as_data(),
            )
        except Exception:
            try:
                await self._git.delete_checkpoint_ref(project.root, ref_name)
            except BridgeError:
                pass
            raise

    async def finalize(
        self,
        project: ProjectSpec,
        checkpoint: CheckpointRecord,
        *,
        expected_after_head: str | None = None,
        expected_after_branch: str | None = None,
    ) -> CheckpointRecord:
        if expected_after_head is not None:
            self.validate_expected_head(expected_after_head)
        if expected_after_branch is not None and (
            not isinstance(expected_after_branch, str)
            or not expected_after_branch
            or any(character in expected_after_branch for character in "\x00\r\n")
        ):
            raise BridgeError("CHECKPOINT_INVALID", "expected Git branch is not valid")
        after = await self._git.snapshot(project.root)
        if expected_after_head is not None and (
            after.head.lower() != expected_after_head.lower()
            or (expected_after_branch is not None and after.branch != expected_after_branch)
        ):
            raise BridgeError(
                "CHECKPOINT_CONFLICT",
                "Git state changed before checkpoint finalization",
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "expected_after_branch": expected_after_branch,
                    "actual_after_branch": after.branch,
                    "expected_after_head": expected_after_head,
                    "actual_after_head": after.head,
                },
            )
        if expected_after_head is None:
            changed_files = await self._git.diff_names_from(project.root, checkpoint.ref_name)
            diff, truncated = await self._git.diff_from(project.root, checkpoint.ref_name)
        else:
            changed_files = await self._git.diff_names_between(
                project.root,
                ref_name=checkpoint.ref_name,
                head=expected_after_head,
            )
            diff, truncated = await self._git.diff_between(
                project.root,
                ref_name=checkpoint.ref_name,
                head=expected_after_head,
            )
            confirmed = await self._git.snapshot(project.root)
            if confirmed.head.lower() != expected_after_head.lower() or (
                expected_after_branch is not None and confirmed.branch != expected_after_branch
            ):
                raise BridgeError(
                    "CHECKPOINT_CONFLICT",
                    "Git state changed during checkpoint finalization",
                    {
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "expected_after_branch": expected_after_branch,
                        "actual_after_branch": confirmed.branch,
                        "expected_after_head": expected_after_head,
                        "actual_after_head": confirmed.head,
                    },
                )
            after = confirmed
        after_data = after.as_data()
        after_data["changed_files"] = list(changed_files)
        after_data["diff_truncated"] = truncated
        return self._database.finalize_checkpoint(
            checkpoint.checkpoint_id,
            after_data=after_data,
            diff_hash=self._git.diff_hash(diff),
        )

    async def determine_commit_mode(
        self,
        project: ProjectSpec,
        *,
        session_id: str | None,
        checkpoint: CheckpointRecord,
    ) -> CommitMode:
        """Select amend only when independent Bridge and Git evidence agrees.

        The caller must invoke this while holding the project's mutation lock.
        Every missing or uncertain fact deliberately falls back to CREATE.
        """

        if (
            not session_id
            or checkpoint.project_id != project.project_id
            or checkpoint.session_id != session_id
            or checkpoint.kind != "mutation"
        ):
            return self._create_mode(
                project.project_id,
                session_id,
                "checkpoint_scope_missing",
            )
        try:
            current = await self._git.status(project.root)
            if current.dirty:
                return self._create_mode(project.project_id, session_id, "worktree_dirty")
            if current.branch != checkpoint.branch:
                return self._create_mode(project.project_id, session_id, "branch_changed")
            if current.head.lower() != checkpoint.head.lower():
                return self._create_mode(project.project_id, session_id, "head_changed")

            candidate = self._database.find_session_wip_checkpoint(
                project_id=project.project_id,
                session_id=session_id,
                branch=current.branch,
                head=current.head,
            )
            if candidate is None:
                return self._create_mode(
                    project.project_id, session_id, "database_evidence_missing"
                )
            if not await self._git.has_session_footer(
                project.root,
                head=current.head,
                session_id=session_id,
            ):
                return self._create_mode(project.project_id, session_id, "session_footer_missing")
            shared_refs = await self._git.shared_refs_containing_head(
                project.root,
                head=current.head,
                branch=current.branch,
            )
            if shared_refs:
                return self._create_mode(project.project_id, session_id, "shared_ref_detected")
            return CommitMode.AMEND_SESSION_WIP
        except Exception as exc:
            reason = getattr(exc, "code", type(exc).__name__)
            return self._create_mode(
                project.project_id, session_id, f"evidence_check_failed:{reason}"
            )

    @staticmethod
    def _create_mode(project_id: str, session_id: str | None, reason: str) -> CommitMode:
        _LOGGER.info(
            "session WIP commit mode fell back to CREATE project_id=%s session_id=%s reason=%s",
            project_id,
            session_id,
            reason,
        )
        return CommitMode.CREATE

    async def verify_ref(self, project: ProjectSpec, checkpoint: CheckpointRecord) -> None:
        try:
            resolved = await self._git.resolve_checkpoint_ref(project.root, checkpoint.ref_name)
        except BridgeError as exc:
            raise BridgeError(
                "CHECKPOINT_INVALID",
                "checkpoint ref is missing or invalid",
                {"checkpoint_id": checkpoint.checkpoint_id},
            ) from exc
        if resolved.lower() != checkpoint.head.lower():
            raise BridgeError(
                "CHECKPOINT_INVALID",
                "checkpoint ref no longer points to its recorded commit",
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "recorded_head": checkpoint.head,
                    "actual_head": resolved,
                },
            )

    def get_for_session(
        self,
        checkpoint_id: str,
        *,
        project_id: str,
        session_id: str,
    ) -> CheckpointRecord:
        checkpoint = self._database.get_checkpoint(checkpoint_id)
        if (
            checkpoint is None
            or checkpoint.owner_id != "local-policy"
            or checkpoint.project_id != project_id
            or checkpoint.session_id != session_id
        ):
            raise BridgeError(
                "CHECKPOINT_NOT_FOUND",
                "checkpoint_id is not available to this project session",
                {"checkpoint_id": checkpoint_id},
            )
        return checkpoint

    def for_operation(self, operation_id: str) -> list[dict[str, Any]]:
        checkpoints = self._database.list_checkpoints(operation_id=operation_id)
        return [self.summary(checkpoint) for checkpoint in checkpoints]

    def mark_restored(self, checkpoint_id: str) -> CheckpointRecord:
        return self._database.mark_checkpoint_restored(checkpoint_id)

    @staticmethod
    def summary(checkpoint: CheckpointRecord) -> dict[str, Any]:
        before = checkpoint.before_data
        after = checkpoint.after_data
        result: dict[str, Any] = {
            "checkpoint_id": checkpoint.checkpoint_id,
            "kind": checkpoint.kind,
            "status": checkpoint.status,
            "branch": checkpoint.branch,
            "ref_name": checkpoint.ref_name,
            "before": {
                "branch": before.get("branch"),
                "head": before.get("head"),
                "dirty": before.get("dirty"),
                "changed_files": before.get("changed_files", []),
                "file_hash_count": len(before.get("file_hashes", {})),
            },
            "diff_hash": checkpoint.diff_hash,
        }
        if after is not None:
            result["after"] = {
                "branch": after.get("branch"),
                "head": after.get("head"),
                "dirty": after.get("dirty"),
                "changed_files": after.get("changed_files", []),
                "file_hash_count": len(after.get("file_hashes", {})),
            }
            result["diff_truncated"] = bool(after.get("diff_truncated", False))
        return result
