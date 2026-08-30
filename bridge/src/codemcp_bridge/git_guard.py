"""Bounded Git inspection and mutation preconditions."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .errors import BridgeError
from .project_registry import is_sensitive_relative_path


@dataclass(frozen=True, slots=True)
class GitStatus:
    branch: str
    head: str
    dirty: bool
    changed_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    branch: str
    head: str
    dirty: bool
    changed_files: tuple[str, ...]
    file_hashes: dict[str, str]

    def as_data(self) -> dict[str, object]:
        return {
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "changed_files": list(self.changed_files),
            "file_hashes": dict(self.file_hashes),
        }


_CHECKPOINT_REF_PATTERN = re.compile(r"^refs/codemcp-remote/checkpoints/[0-9a-f]{32}$")
_HEAD_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SESSION_FOOTER_PREFIX = "Codemcp-Remote-Session: "
_EXPECTED_REF_PREFIXES = ("refs/heads/", "refs/remotes/", "refs/tags/")


class CommitMode(StrEnum):
    """The only commit side effects a Bridge mutation may request."""

    CREATE = "create"
    AMEND_SESSION_WIP = "amend_session_wip"


def _truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.communicate(), timeout=5)
        except (TimeoutError, OSError):
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except (TimeoutError, ProcessLookupError):
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


class GitGuard:
    """Run fixed Git inspection commands with stdin closed and bounded output."""

    def __init__(self, *, timeout_seconds: float = 30, max_output_bytes: int = 262_144):
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def _run(self, project_root: Path, *arguments: str) -> str:
        process_kwargs: dict[str, object] = {}
        if os.name != "nt":
            process_kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *arguments,
                cwd=project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **process_kwargs,
            )
        except OSError as exc:
            raise BridgeError(
                "BACKEND_UNAVAILABLE",
                "Git is not available for the registered project",
                {"project_id": None},
                retryable=True,
                status="failed",
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as exc:
            await _terminate_process(process)
            raise BridgeError(
                "BACKEND_UNAVAILABLE",
                "Git command timed out",
                {"command": ["git", *arguments]},
                retryable=True,
                status="failed",
            ) from exc
        output = stdout.decode("utf-8", errors="replace")
        error_output = stderr.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail, _ = _truncate_text(error_output, 2_000)
            raise BridgeError(
                "BACKEND_UNAVAILABLE",
                "Git command failed",
                {
                    "command": ["git", *arguments],
                    "returncode": process.returncode,
                    "stderr": detail,
                },
                status="failed",
            )
        return output

    async def status(self, project_root: Path) -> GitStatus:
        branch = (await self._run(project_root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        head = (await self._run(project_root, "rev-parse", "HEAD")).strip()
        porcelain = await self._run(
            project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "-z",
        )
        entries = porcelain.split("\0")
        changed: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            if len(entry) < 3:
                index += 1
                continue
            status_code = entry[:2]
            changed.append(entry[3:])
            if "R" in status_code or "C" in status_code:
                index += 1
                if index < len(entries) and entries[index]:
                    changed.append(entries[index])
            index += 1
        changed_files = tuple(changed)
        return GitStatus(
            branch=branch,
            head=head,
            dirty=bool(changed_files),
            changed_files=changed_files,
        )

    async def require_worktree_root(self, project_root: Path) -> None:
        actual = Path(
            (await self._run(project_root, "rev-parse", "--show-toplevel")).strip()
        ).resolve(strict=False)
        expected = project_root.resolve(strict=False)
        if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
            raise BridgeError(
                "PROJECT_NOT_ALLOWED",
                "registered project root must be the Git worktree root",
                {"expected_root": str(expected), "actual_root": str(actual)},
            )

    async def snapshot(self, project_root: Path) -> GitSnapshot:
        status = await self.status(project_root)
        tree = await self._run(project_root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
        file_hashes: dict[str, str] = {}
        for entry in tree.split("\0"):
            if "\t" not in entry:
                continue
            header, path = entry.split("\t", 1)
            fields = header.split()
            if len(fields) == 3 and fields[1] == "blob":
                file_hashes[path] = fields[2]
        return GitSnapshot(
            branch=status.branch,
            head=status.head,
            dirty=status.dirty,
            changed_files=status.changed_files,
            file_hashes=file_hashes,
        )

    @staticmethod
    def diff_hash(diff: str) -> str:
        return hashlib.sha256(diff.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _require_checkpoint_ref(ref_name: str) -> None:
        if not _CHECKPOINT_REF_PATTERN.fullmatch(ref_name):
            raise BridgeError("CHECKPOINT_INVALID", "checkpoint ref is not Bridge-owned")

    @staticmethod
    def _require_head(head: str) -> None:
        if not isinstance(head, str) or not _HEAD_PATTERN.fullmatch(head):
            raise BridgeError("CHECKPOINT_INVALID", "checkpoint head is not a Git commit")

    @staticmethod
    def _reject_sensitive_names(names: tuple[str, ...] | list[str]) -> None:
        sensitive_names = [name for name in names if is_sensitive_relative_path(name)]
        if sensitive_names:
            raise BridgeError(
                "SENSITIVE_PATH",
                "diff includes sensitive paths and is not exposed",
                {"paths": sensitive_names},
            )

    async def create_checkpoint_ref(self, project_root: Path, ref_name: str, head: str) -> None:
        self._require_checkpoint_ref(ref_name)
        self._require_head(head)
        await self._run(project_root, "update-ref", "--no-deref", ref_name, head)

    async def delete_checkpoint_ref(self, project_root: Path, ref_name: str) -> None:
        self._require_checkpoint_ref(ref_name)
        await self._run(project_root, "update-ref", "--no-deref", "-d", ref_name)

    async def resolve_checkpoint_ref(self, project_root: Path, ref_name: str) -> str:
        self._require_checkpoint_ref(ref_name)
        resolved = (
            await self._run(
                project_root,
                "rev-parse",
                "--verify",
                f"{ref_name}^{{commit}}",
            )
        ).strip()
        self._require_head(resolved)
        return resolved

    async def reset_to_checkpoint(self, project_root: Path, ref_name: str) -> None:
        self._require_checkpoint_ref(ref_name)
        await self._run(project_root, "reset", "--hard", ref_name)

    @staticmethod
    def session_footer(session_id: str) -> str:
        if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise BridgeError(
                "INVALID_REQUEST",
                "session_id cannot be encoded in a Git commit footer",
            )
        return f"{_SESSION_FOOTER_PREFIX}{session_id}"

    @staticmethod
    def _parse_session_footer(message: str) -> str | None:
        normalized = message.rstrip("\r\n")
        if not normalized:
            return None
        lines = normalized.splitlines()
        footer_lines = [line for line in lines if line.startswith(_SESSION_FOOTER_PREFIX)]
        if len(footer_lines) != 1 or lines[-1] != footer_lines[0]:
            return None
        session_id = footer_lines[0][len(_SESSION_FOOTER_PREFIX) :]
        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            return None
        return session_id

    async def read_session_footer(self, project_root: Path, *, head: str) -> str | None:
        """Read only the exact Bridge session footer from a Git tip."""

        self._require_head(head)
        message = await self._run(
            project_root,
            "show",
            "--no-patch",
            "--no-notes",
            "--format=%B",
            head,
        )
        return self._parse_session_footer(message)

    async def has_session_footer(
        self,
        project_root: Path,
        *,
        head: str,
        session_id: str,
    ) -> bool:
        expected = self.session_footer(session_id)
        actual = await self.read_session_footer(project_root, head=head)
        return actual == expected[len(_SESSION_FOOTER_PREFIX) :]

    async def shared_refs_containing_head(
        self,
        project_root: Path,
        *,
        head: str,
        branch: str,
    ) -> tuple[str, ...]:
        """Return refs other than the current branch that publish/share a tip.

        The namespaces are fixed here.  In particular, checkpoint refs are
        deliberately not inspected and cannot block a safe session amend.
        """

        self._require_head(head)
        if (
            not isinstance(branch, str)
            or not branch
            or any(character in branch for character in "\x00\r\n")
        ):
            raise BridgeError("CHECKPOINT_INVALID", "Git branch is not valid")
        current_ref = None if branch == "HEAD" else f"refs/heads/{branch}"
        output = await self._run(
            project_root,
            "for-each-ref",
            "--format=%(refname)",
            "--contains",
            head,
            "refs/heads",
            "refs/remotes",
            "refs/tags",
        )
        refs = tuple(line.strip() for line in output.splitlines() if line.strip())
        shared: list[str] = []
        for ref in refs:
            if ref.startswith("refs/codemcp-remote/checkpoints/"):
                continue
            if not ref.startswith(_EXPECTED_REF_PREFIXES):
                raise BridgeError(
                    "BACKEND_UNAVAILABLE",
                    "Git ref inspection returned an unexpected ref",
                    status="failed",
                )
            if ref == current_ref:
                continue
            shared.append(ref)
        return tuple(shared)

    async def has_shared_refs_containing_head(
        self,
        project_root: Path,
        *,
        head: str,
        branch: str,
    ) -> bool:
        return bool(
            await self.shared_refs_containing_head(
                project_root,
                head=head,
                branch=branch,
            )
        )

    async def _verify_amend_target(
        self,
        project_root: Path,
        *,
        expected_head: str,
        expected_branch: str | None = None,
    ) -> None:
        """Recheck the amend target immediately before the Git side effect."""

        current = await self.status(project_root)
        if current.head.lower() != expected_head.lower() or (
            expected_branch is not None and current.branch != expected_branch
        ):
            raise BridgeError(
                "CONFLICT",
                "Git branch or HEAD changed before the session WIP amend started",
                {
                    "expected_branch": expected_branch,
                    "actual_branch": current.branch,
                    "expected_head": expected_head,
                    "actual_head": current.head,
                },
            )
        shared_refs = await self.shared_refs_containing_head(
            project_root,
            head=current.head,
            branch=current.branch,
        )
        if shared_refs:
            raise BridgeError(
                "CONFLICT",
                "session WIP amend is blocked by locally observable shared refs",
                {"shared_refs": list(shared_refs), "head": current.head},
            )

    @staticmethod
    def _validate_commit_paths(
        paths: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        if isinstance(paths, (str, bytes)):
            raise BridgeError("INVALID_REQUEST", "commit paths must be a sequence")
        commit_paths = tuple(paths)
        if not commit_paths or any(
            not isinstance(path, str) or not path or "\x00" in path for path in commit_paths
        ):
            raise BridgeError("INVALID_REQUEST", "at least one valid commit path is required")
        return commit_paths

    def _commit_arguments(
        self,
        *,
        paths: tuple[str, ...] | list[str],
        commit_mode: CommitMode,
        description: str | None,
        session_id: str | None,
    ) -> tuple[str, ...]:
        if not isinstance(commit_mode, CommitMode):
            raise BridgeError("INVALID_REQUEST", "commit_mode is not a supported Bridge mode")
        commit_paths = self._validate_commit_paths(paths)
        arguments = ["commit"]
        if commit_mode == CommitMode.CREATE:
            if (
                not isinstance(description, str)
                or not description
                or len(description) > 500
                or any(character in description for character in "\r\n\x00")
                or not isinstance(session_id, str)
            ):
                raise BridgeError("INVALID_REQUEST", "description and session_id are required")
            arguments.extend(
                [
                    "-m",
                    f"wip: {description}\n\n{self.session_footer(session_id)}",
                ]
            )
        elif commit_mode == CommitMode.AMEND_SESSION_WIP:
            arguments.extend(["--amend", "--no-edit", "--allow-empty"])
        else:
            raise BridgeError("INVALID_REQUEST", "commit_mode is not a supported Bridge mode")
        arguments.extend(["--only", "--", *commit_paths])
        return tuple(arguments)

    async def commit_paths(
        self,
        project_root: Path,
        *,
        paths: tuple[str, ...] | list[str],
        commit_mode: CommitMode = CommitMode.CREATE,
        description: str | None = None,
        session_id: str | None = None,
        expected_head: str | None = None,
        expected_branch: str | None = None,
    ) -> str:
        """Apply one explicit, fixed-argv commit mode to selected paths."""

        arguments = self._commit_arguments(
            paths=paths,
            commit_mode=commit_mode,
            description=description,
            session_id=session_id,
        )
        if expected_head is not None:
            self._require_head(expected_head)
        before = await self.status(project_root)
        if expected_head is not None and before.head.lower() != expected_head.lower():
            raise BridgeError(
                "CONFLICT",
                "Git HEAD changed before the WIP commit started",
                {"expected_head": expected_head, "actual_head": before.head},
            )
        try:
            if commit_mode == CommitMode.AMEND_SESSION_WIP:
                await self._verify_amend_target(
                    project_root,
                    expected_head=expected_head or before.head,
                    expected_branch=expected_branch,
                )
            await self._run(
                project_root,
                *arguments,
            )
            final = await self.status(project_root)
            if final.dirty or (
                expected_head is not None and final.head.lower() == expected_head.lower()
            ):
                raise BridgeError(
                    "CONFLICT",
                    "WIP commit did not finalize to a new clean Git baseline",
                    {
                        "expected_previous_head": expected_head,
                        "actual_head": final.head,
                        "changed_files": list(final.changed_files),
                    },
                )
            return final.head
        except BridgeError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "WIP commit outcome is unknown and requires reconciliation",
                {"cause": exc.code},
                status="unknown",
            ) from exc

    async def create_wip_commit(
        self,
        project_root: Path,
        *,
        paths: tuple[str, ...] | list[str],
        description: str,
        session_id: str,
        expected_head: str | None = None,
        expected_branch: str | None = None,
    ) -> str:
        """Create a Bridge WIP commit with a stable session ownership footer."""

        return await self.commit_paths(
            project_root,
            paths=paths,
            commit_mode=CommitMode.CREATE,
            description=description,
            session_id=session_id,
            expected_head=expected_head,
            expected_branch=expected_branch,
        )

    async def commit_file_bytes(
        self,
        project_root: Path,
        *,
        path: str,
        content: bytes,
        expected_head: str,
        description: str,
        require_exists: bool | None = None,
        commit_mode: CommitMode | None = None,
        session_id: str | None = None,
        expected_branch: str | None = None,
    ) -> str:
        """Atomically replace one file and commit only that path.

        ``commit_mode`` is optional for compatibility with the pre-session-WIP
        low-level helper. Bridge mutation callers pass it explicitly so a new
        commit carries the session footer and only proven WIP commits amend.
        """

        self._require_head(expected_head)
        if commit_mode is None:
            if session_id is not None:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "session_id requires an explicit commit_mode",
                )
            legacy_paths = self._validate_commit_paths((path,))
            commit_arguments = (
                "commit",
                "-m",
                f"wip: {description}",
                "--only",
                "--",
                *legacy_paths,
            )
        else:
            commit_arguments = self._commit_arguments(
                paths=(path,),
                commit_mode=commit_mode,
                description=description,
                session_id=session_id,
            )
        before = await self.status(project_root)
        if before.head.lower() != expected_head.lower():
            raise BridgeError(
                "CONFLICT",
                "Git HEAD changed before the file mutation started",
                {"expected_head": expected_head, "actual_head": before.head},
            )
        if before.dirty:
            raise BridgeError(
                "WORKSPACE_DIRTY",
                "file mutation requires a clean workspace",
                {"changed_files": list(before.changed_files)},
            )

        target = project_root / path
        if require_exists is True and not target.is_file():
            raise BridgeError("FILE_NOT_FOUND", "a regular file is required", {"path": path})
        if require_exists is False and target.exists():
            raise BridgeError("CONFLICT", "file already exists", {"path": path})
        if target.exists() and not target.is_file():
            raise BridgeError("CONFLICT", "target path is not a regular file", {"path": path})

        if target.exists():
            try:
                if target.read_bytes() == content:
                    return before.head
            except OSError as exc:
                raise BridgeError(
                    "BACKEND_UNAVAILABLE",
                    "target file could not be read before mutation",
                    {"path": path},
                    retryable=True,
                    status="failed",
                ) from exc

        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=".codemcp-remote-write-",
                dir=target.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            temp_path = None
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "file write outcome is unknown and requires reconciliation",
                {"path": path},
                status="unknown",
            ) from exc

        try:
            after_write = await self.status(project_root)
            unexpected_paths = sorted(set(after_write.changed_files) - {path})
            if after_write.head.lower() != expected_head.lower() or unexpected_paths:
                raise BridgeError(
                    "CONFLICT",
                    "Git state changed concurrently while writing the file",
                    {
                        "expected_head": expected_head,
                        "actual_head": after_write.head,
                        "unexpected_changed_files": unexpected_paths,
                    },
                )
            if not after_write.dirty:
                return after_write.head
            if path not in after_write.changed_files:
                raise BridgeError(
                    "CONFLICT",
                    "target file mutation was not isolated to the expected path",
                    {"path": path, "changed_files": list(after_write.changed_files)},
                )

            await self._run(project_root, "add", "--", path)
            if commit_mode == CommitMode.AMEND_SESSION_WIP:
                await self._verify_amend_target(
                    project_root,
                    expected_head=expected_head,
                    expected_branch=expected_branch,
                )
            await self._run(project_root, *commit_arguments)
            final = await self.status(project_root)
            if final.dirty or final.head.lower() == expected_head.lower():
                raise BridgeError(
                    "CONFLICT",
                    "file mutation did not finalize to a new clean Git baseline",
                    {
                        "expected_previous_head": expected_head,
                        "actual_head": final.head,
                        "changed_files": list(final.changed_files),
                    },
                )
            return final.head
        except BridgeError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "file mutation changed the worktree but could not be safely finalized",
                {"path": path, "cause": exc.code},
                status="unknown",
            ) from exc

    async def move_tracked_file(
        self,
        project_root: Path,
        *,
        source: str,
        destination: str,
        expected_head: str,
        description: str | None = None,
        commit_mode: CommitMode = CommitMode.CREATE,
        session_id: str | None = None,
        expected_branch: str | None = None,
    ) -> str:
        """Move one tracked file using an explicit create or safe amend mode."""

        self._require_head(expected_head)
        commit_arguments = self._commit_arguments(
            paths=(source, destination),
            commit_mode=commit_mode,
            description=description,
            session_id=session_id,
        )
        before = await self.status(project_root)
        if before.head.lower() != expected_head.lower():
            raise BridgeError(
                "CONFLICT",
                "Git HEAD changed before the file move started",
                {"expected_head": expected_head, "actual_head": before.head},
            )
        if before.dirty:
            raise BridgeError(
                "WORKSPACE_DIRTY",
                "file move requires a clean workspace",
                {"changed_files": list(before.changed_files)},
            )

        try:
            await self._run(project_root, "mv", "--", source, destination)
        except BridgeError as exc:
            raise BridgeError(
                "CONFLICT",
                "Git could not move the tracked source file",
                {"source_path": source, "destination_path": destination},
                status="failed",
            ) from exc

        try:
            staged = await self.status(project_root)
            expected_paths = {source, destination}
            unexpected_paths = sorted(set(staged.changed_files) - expected_paths)
            if staged.head.lower() != expected_head.lower() or unexpected_paths:
                raise BridgeError(
                    "CONFLICT",
                    "Git state changed concurrently while moving the file",
                    {
                        "expected_head": expected_head,
                        "actual_head": staged.head,
                        "unexpected_changed_files": unexpected_paths,
                    },
                )
            if commit_mode == CommitMode.AMEND_SESSION_WIP:
                await self._verify_amend_target(
                    project_root,
                    expected_head=expected_head,
                    expected_branch=expected_branch,
                )
            await self._run(project_root, *commit_arguments)
            final = await self.status(project_root)
            if final.dirty or final.head.lower() == expected_head.lower():
                raise BridgeError(
                    "CONFLICT",
                    "file move did not finalize to a new clean Git baseline",
                    {
                        "expected_previous_head": expected_head,
                        "actual_head": final.head,
                        "changed_files": list(final.changed_files),
                    },
                )
            return final.head
        except BridgeError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "file move changed the worktree but could not be safely finalized",
                {
                    "source_path": source,
                    "destination_path": destination,
                    "cause": exc.code,
                },
                status="unknown",
            ) from exc

    async def delete_tracked_file(
        self,
        project_root: Path,
        *,
        path: str,
        expected_head: str,
        description: str | None = None,
        commit_mode: CommitMode = CommitMode.CREATE,
        session_id: str | None = None,
        expected_branch: str | None = None,
    ) -> str:
        """Delete one tracked file using an explicit create or safe amend mode."""

        self._require_head(expected_head)
        commit_arguments = self._commit_arguments(
            paths=(path,),
            commit_mode=commit_mode,
            description=description,
            session_id=session_id,
        )
        before = await self.status(project_root)
        if before.head.lower() != expected_head.lower():
            raise BridgeError(
                "CONFLICT",
                "Git HEAD changed before the file delete started",
                {"expected_head": expected_head, "actual_head": before.head},
            )
        if before.dirty:
            raise BridgeError(
                "WORKSPACE_DIRTY",
                "file delete requires a clean workspace",
                {"changed_files": list(before.changed_files)},
            )

        try:
            await self._run(project_root, "rm", "--", path)
        except BridgeError as exc:
            raise BridgeError(
                "CONFLICT",
                "Git could not delete the tracked source file",
                {"path": path},
                status="failed",
            ) from exc

        try:
            staged = await self.status(project_root)
            unexpected_paths = sorted(set(staged.changed_files) - {path})
            if staged.head.lower() != expected_head.lower() or unexpected_paths:
                raise BridgeError(
                    "CONFLICT",
                    "Git state changed concurrently while deleting the file",
                    {
                        "expected_head": expected_head,
                        "actual_head": staged.head,
                        "unexpected_changed_files": unexpected_paths,
                    },
                )
            if commit_mode == CommitMode.AMEND_SESSION_WIP:
                await self._verify_amend_target(
                    project_root,
                    expected_head=expected_head,
                    expected_branch=expected_branch,
                )
            await self._run(project_root, *commit_arguments)
            final = await self.status(project_root)
            if final.dirty or final.head.lower() == expected_head.lower():
                raise BridgeError(
                    "CONFLICT",
                    "file delete did not finalize to a new clean Git baseline",
                    {
                        "expected_previous_head": expected_head,
                        "actual_head": final.head,
                        "changed_files": list(final.changed_files),
                    },
                )
            return final.head
        except BridgeError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "file delete changed the worktree but could not be safely finalized",
                {
                    "path": path,
                    "cause": exc.code,
                },
                status="unknown",
            ) from exc

    async def diff(self, project_root: Path) -> tuple[str, bool]:
        status = await self.status(project_root)
        self._reject_sensitive_names(status.changed_files)
        changed_names = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        )
        self._reject_sensitive_names([name for name in changed_names.split("\0") if name])
        output = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            "HEAD",
            "--",
        )
        return _truncate_text(output, self._max_output_bytes)

    async def diff_names_from(self, project_root: Path, ref_name: str) -> tuple[str, ...]:
        self._require_checkpoint_ref(ref_name)
        status = await self.status(project_root)
        self._reject_sensitive_names(status.changed_files)
        changed_names = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            ref_name,
            "--",
        )
        names = tuple(name for name in changed_names.split("\0") if name)
        self._reject_sensitive_names(names)
        return names

    async def diff_names_between(
        self,
        project_root: Path,
        *,
        ref_name: str,
        head: str,
    ) -> tuple[str, ...]:
        """Return changed paths between a checkpoint ref and a fixed commit."""

        self._require_checkpoint_ref(ref_name)
        self._require_head(head)
        changed_names = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            ref_name,
            head,
            "--",
        )
        names = tuple(name for name in changed_names.split("\0") if name)
        self._reject_sensitive_names(names)
        return names

    async def diff_from(self, project_root: Path, ref_name: str) -> tuple[str, bool]:
        self._require_checkpoint_ref(ref_name)
        await self.diff_names_from(project_root, ref_name)
        output = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            ref_name,
            "--",
        )
        return _truncate_text(output, self._max_output_bytes)

    async def diff_between(
        self,
        project_root: Path,
        *,
        ref_name: str,
        head: str,
    ) -> tuple[str, bool]:
        """Return a bounded diff between a checkpoint ref and a fixed commit."""

        self._require_checkpoint_ref(ref_name)
        self._require_head(head)
        await self.diff_names_between(project_root, ref_name=ref_name, head=head)
        output = await self._run(
            project_root,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
            ref_name,
            head,
            "--",
        )
        return _truncate_text(output, self._max_output_bytes)
