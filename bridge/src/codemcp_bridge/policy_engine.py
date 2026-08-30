"""Policy checks shared by MCP tools and the codemcp Adapter."""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

from .errors import BridgeError
from .generated_codemcp import can_generate_codemcp_config
from .git_guard import GitGuard, GitStatus
from .project_registry import ProjectRegistry
from .settings import BridgeSettings, CommandSpec, ProjectSpec


class PolicyEngine:
    def __init__(self, settings: BridgeSettings, registry: ProjectRegistry, git: GitGuard):
        self._settings = settings
        self._registry = registry
        self._git = git

    async def inspect_project(
        self, project: ProjectSpec, *, enforce_branch: bool = True
    ) -> GitStatus:
        if not project.root.is_dir():
            raise BridgeError(
                "PROJECT_NOT_ALLOWED",
                "registered project root does not exist",
                {"project_id": project.project_id},
            )
        await self._git.require_worktree_root(project.root)
        status = await self._git.status(project.root)
        if enforce_branch:
            self.require_allowed_branch(project, status.branch)
        return status

    @staticmethod
    def require_allowed_branch(project: ProjectSpec, branch: str) -> None:
        if not any(fnmatch.fnmatchcase(branch, pattern) for pattern in project.allowed_branches):
            raise BridgeError(
                "BRANCH_NOT_ALLOWED",
                "current branch is not allowed for this project",
                {"project_id": project.project_id, "branch": branch},
            )

    def require_clean_workspace(self, project: ProjectSpec, status: GitStatus) -> GitStatus:
        if (
            self._settings.policy.require_clean_workspace or project.require_clean_workspace
        ) and status.dirty:
            raise BridgeError(
                "WORKSPACE_DIRTY",
                "mutation requires a clean workspace",
                {"changed_files": list(status.changed_files)},
            )
        return status

    async def require_mutation_preconditions(
        self, project: ProjectSpec, *, enforce_branch: bool = True
    ) -> GitStatus:
        status = await self.inspect_project(project, enforce_branch=enforce_branch)
        return self.require_clean_workspace(project, status)

    def registered_command(
        self,
        project: ProjectSpec,
        command_id: str,
        *,
        require_approval: bool = True,
    ) -> CommandSpec:
        if self._settings.policy.allow_arbitrary_commands:
            raise BridgeError("COMMAND_NOT_ALLOWED", "arbitrary commands are disabled by policy")
        command = project.commands.get(command_id)
        if command is None:
            raise BridgeError(
                "COMMAND_NOT_ALLOWED",
                "command_id is not registered for this project",
                {"command_id": command_id},
            )
        if require_approval and command.approval == "required":
            raise BridgeError(
                "APPROVAL_REQUIRED",
                "the registered command requires explicit approval",
                {"command_id": command_id},
            )
        self._verify_codemcp_command(project, command)
        return command

    def command(
        self,
        project: ProjectSpec,
        command_id: str,
        expected_kind: str,
        *,
        require_approval: bool = True,
    ) -> CommandSpec:
        if self._settings.policy.allow_arbitrary_commands:
            raise BridgeError("COMMAND_NOT_ALLOWED", "arbitrary commands are disabled by policy")
        command = project.commands.get(command_id)
        if command is None or command.kind != expected_kind:
            raise BridgeError(
                "COMMAND_NOT_ALLOWED",
                "command_id is not registered for this operation",
                {"command_id": command_id, "expected_kind": expected_kind},
            )
        if require_approval and command.approval == "required":
            raise BridgeError(
                "APPROVAL_REQUIRED",
                "the registered command requires explicit approval",
                {"command_id": command_id},
            )
        self._verify_codemcp_command(project, command)
        return command

    def _verify_codemcp_command(self, project: ProjectSpec, command: CommandSpec) -> None:
        try:
            project.codemcp_config.lstat()
        except FileNotFoundError:
            if can_generate_codemcp_config(project):
                return
        except OSError:
            pass

        try:
            relative = project.codemcp_config.relative_to(project.root).as_posix()
            _, config_path, _ = self._registry.resolve_path(
                project.project_id,
                relative,
                allow_root=False,
                reject_sensitive=False,
            )
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            raise BridgeError(
                "COMMAND_NOT_ALLOWED",
                "project codemcp configuration cannot be verified",
                {"command_id": command.command_id},
            ) from exc
        commands = config.get("commands", {})
        configured = commands.get(command.command_id, {}) if isinstance(commands, dict) else {}
        configured_argv = configured.get("command") if isinstance(configured, dict) else None
        if configured_argv != list(command.argv):
            raise BridgeError(
                "COMMAND_NOT_ALLOWED",
                "registered command does not match codemcp.toml",
                {"command_id": command.command_id},
            )

    def validate_file_size(self, path: Path) -> int:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise BridgeError("FILE_NOT_FOUND", "file does not exist") from exc
        if size > self._settings.policy.max_file_bytes:
            raise BridgeError(
                "FILE_TOO_LARGE",
                "file exceeds the configured size limit",
                {"max_file_bytes": self._settings.policy.max_file_bytes},
            )
        return size

    @staticmethod
    def require_text_file(path: Path) -> None:
        try:
            with path.open("rb") as handle:
                sample = handle.read(4096)
        except OSError as exc:
            raise BridgeError("FILE_NOT_FOUND", "file cannot be read") from exc
        if b"\x00" in sample:
            raise BridgeError("BINARY_FILE", "binary files are not exposed by the Bridge")

    @staticmethod
    def require_regular_file(path: Path) -> None:
        if not path.exists() or not path.is_file():
            raise BridgeError("FILE_NOT_FOUND", "a regular file is required")

    @staticmethod
    def validate_pattern(pattern: str) -> None:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            raise BridgeError("INVALID_REQUEST", "pattern must be 1-500 characters")
