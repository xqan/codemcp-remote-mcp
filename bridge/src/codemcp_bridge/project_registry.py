"""Project and path authorization for the Bridge."""

from __future__ import annotations

import fnmatch
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import BridgeError
from .settings import (
    BridgeSettings,
    ProjectSpec,
    SettingsError,
    load_projects,
    normalize_relative_path,
    to_wsl_path,
)

SENSITIVE_NAMES = {
    ".git",
    ".codemcp",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "password",
    "passwd",
    "secret",
    "secrets",
    "token",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}
SENSITIVE_GLOBS = tuple(
    SENSITIVE_NAMES | {"*.env", "*.env.*"} | {f"*{suffix}" for suffix in SENSITIVE_SUFFIXES}
)


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _iter_existing_components(root: Path, candidate: Path):
    current = root
    yield current
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            yield current


def _is_sensitive(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    for part in parts:
        lower = part.lower()
        if any(fnmatch.fnmatchcase(lower, pattern) for pattern in SENSITIVE_GLOBS):
            return True
    return False


def is_sensitive_relative_path(relative_path: str) -> bool:
    return _is_sensitive(relative_path)


@dataclass(frozen=True, slots=True)
class ProjectRegistryFingerprint:
    """Cheap identity for one observed projects.toml version."""

    mtime_ns: int
    size: int


def _projects_config_fingerprint(path: Path) -> ProjectRegistryFingerprint:
    try:
        info = path.stat()
    except OSError as exc:
        raise SettingsError(f"cannot stat project configuration: {path}") from exc
    return ProjectRegistryFingerprint(mtime_ns=info.st_mtime_ns, size=info.st_size)


class ProjectRegistry:
    """Resolve only registered project IDs and safe project-relative paths."""

    def __init__(self, settings: BridgeSettings):
        self._settings = settings
        self._projects_config_path = settings.projects_config_path
        self._projects = dict(settings.projects)
        self._reload_lock = threading.RLock()
        self._generation = 1
        self._last_reload_status = "initial"
        self._last_reload_error: str | None = None
        self._last_reload_error_code: str | None = None
        self._last_failed_fingerprint: ProjectRegistryFingerprint | None = None
        try:
            self._fingerprint: ProjectRegistryFingerprint | None = _projects_config_fingerprint(
                self._projects_config_path
            )
        except SettingsError as exc:
            self._fingerprint = None
            self._last_reload_status = "failed"
            self._last_reload_error = str(exc)
            self._last_reload_error_code = "projects_config_unavailable"

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_reload_status(self) -> str:
        return self._last_reload_status

    @property
    def last_reload_error(self) -> str | None:
        return self._last_reload_error

    @property
    def last_reload_error_code(self) -> str | None:
        """Return a public-safe reload error classification without paths or TOML content."""

        return self._last_reload_error_code

    def snapshot(self) -> dict[str, ProjectSpec]:
        """Return an isolated copy of the current validated project snapshot."""

        with self._reload_lock:
            return dict(self._projects)

    def refresh_if_changed(self) -> bool:
        """Install one coherent validated project snapshot when the file changes."""

        try:
            observed = _projects_config_fingerprint(self._projects_config_path)
        except SettingsError as exc:
            with self._reload_lock:
                self._last_reload_status = "failed"
                self._last_reload_error = str(exc)
                self._last_reload_error_code = "projects_config_unavailable"
            return False

        if observed == self._fingerprint or observed == self._last_failed_fingerprint:
            return False

        with self._reload_lock:
            try:
                observed = _projects_config_fingerprint(self._projects_config_path)
            except SettingsError as exc:
                self._last_reload_status = "failed"
                self._last_reload_error = str(exc)
                self._last_reload_error_code = "projects_config_unavailable"
                return False
            if observed == self._fingerprint or observed == self._last_failed_fingerprint:
                return False

            try:
                candidate = load_projects(self._projects_config_path)
            except SettingsError as exc:
                self._last_failed_fingerprint = observed
                self._last_reload_status = "failed"
                self._last_reload_error = str(exc)
                self._last_reload_error_code = "projects_config_invalid"
                return False

            for project_id in self._projects.keys() & candidate.keys():
                current_root = self._projects[project_id].root.resolve(strict=False)
                candidate_root = candidate[project_id].root.resolve(strict=False)
                if current_root != candidate_root:
                    self._last_failed_fingerprint = observed
                    self._last_reload_status = "failed"
                    self._last_reload_error = (
                        f"project root change requires remove then add: {project_id}"
                    )
                    self._last_reload_error_code = "project_root_change_requires_remove_add"
                    return False

            try:
                verified = _projects_config_fingerprint(self._projects_config_path)
            except SettingsError as exc:
                self._last_reload_status = "failed"
                self._last_reload_error = str(exc)
                self._last_reload_error_code = "projects_config_unavailable"
                return False
            if verified != observed:
                self._last_reload_status = "failed"
                self._last_reload_error = "project configuration changed during reload"
                self._last_reload_error_code = "projects_config_changed_during_reload"
                return False

            self._projects = dict(candidate)
            self._fingerprint = verified
            self._last_failed_fingerprint = None
            self._generation += 1
            self._last_reload_status = "ok"
            self._last_reload_error = None
            self._last_reload_error_code = None
            return True

    def get(self, project_id: str) -> ProjectSpec:
        project = self._projects.get(project_id)
        if project is None:
            raise BridgeError(
                "PROJECT_NOT_ALLOWED",
                "project_id is not registered",
                {"project_id": project_id},
            )
        return project

    def resolve_path(
        self,
        project_id: str,
        relative_path: str | None,
        *,
        allow_root: bool = False,
        reject_sensitive: bool = True,
    ) -> tuple[ProjectSpec, Path, str]:
        project = self.get(project_id)
        if relative_path is None or relative_path in {"", "."}:
            if not allow_root:
                raise BridgeError("INVALID_REQUEST", "path is required", {"field": "path"})
            normalized = "."
            candidate = project.root
        else:
            try:
                normalized = normalize_relative_path(relative_path)
            except ValueError as exc:
                raise BridgeError("PATH_ESCAPE", str(exc)) from exc
            if reject_sensitive and _is_sensitive(normalized):
                raise BridgeError(
                    "SENSITIVE_PATH",
                    "access to sensitive paths is denied",
                    {"path": normalized},
                )
            candidate = project.root.joinpath(*PurePosixPath(normalized).parts)

        root = project.root.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
        if root != resolved and root not in resolved.parents:
            raise BridgeError("PATH_ESCAPE", "path escapes the registered project root")
        if any(
            _is_reparse_point(item) for item in _iter_existing_components(project.root, candidate)
        ):
            raise BridgeError("PATH_ESCAPE", "symlink or reparse-point paths are denied")
        return project, candidate, normalized

    def worker_path(self, path: Path) -> str:
        if os.name == "nt" and self._settings.codemcp.worker_mode == "wsl2":
            return to_wsl_path(path)
        return str(path)

    def safe_search_paths(self, project: ProjectSpec, target: Path) -> list[Path]:
        """Return search roots that cannot recursively include sensitive paths.

        codemcp's Grep accepts one include pathspec but no exclude pathspec. Split
        only directories that contain an excluded descendant, keeping the normal
        one-call path for projects without sensitive files.
        """

        try:
            if not target.is_dir():
                return [target]
        except OSError:
            return []
        paths, excluded = self._safe_search_paths(project, target)
        return paths if excluded else [target]

    def _safe_search_paths(self, project: ProjectSpec, target: Path) -> tuple[list[Path], bool]:
        try:
            entries = sorted(target.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return [], True

        safe_children: list[Path] = []
        excluded = False
        for entry in entries:
            relative = entry.relative_to(project.root).as_posix()
            # Git never searches its own metadata directory, so it need not
            # force every otherwise-safe project directory into file-level calls.
            if entry.name.lower() == ".git":
                continue
            if _is_reparse_point(entry) or is_sensitive_relative_path(relative):
                excluded = True
                continue
            try:
                if entry.is_dir():
                    child_paths, child_excluded = self._safe_search_paths(project, entry)
                    safe_children.extend(child_paths)
                    excluded = excluded or child_excluded
                elif entry.is_file():
                    safe_children.append(entry)
                else:
                    excluded = True
            except OSError:
                excluded = True

        if not excluded:
            return [target], False
        return safe_children, True

    def safe_directory_tree(
        self,
        project: ProjectSpec,
        target: Path,
        *,
        max_entries: int = 1000,
    ) -> tuple[list[tuple[str, bool]], bool]:
        """Return a bounded recursive tree without exposing sensitive or linked paths."""

        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name.lower(), reverse=True)
        except OSError as exc:
            raise BridgeError(
                "BACKEND_UNAVAILABLE",
                "directory could not be listed",
                {"path": self.relative_path(project, target)},
            ) from exc

        stack: list[tuple[Path, str]] = [(child, child.name) for child in children]
        entries: list[tuple[str, bool]] = []
        while stack:
            entry, relative_to_target = stack.pop()
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue

            project_relative = entry.relative_to(project.root).as_posix()
            if _is_reparse_point(entry) or is_sensitive_relative_path(project_relative):
                continue

            try:
                is_directory = entry.is_dir()
                is_file = entry.is_file()
            except OSError:
                continue
            if not is_directory and not is_file:
                continue
            if len(entries) >= max_entries:
                return entries, True

            normalized = PurePosixPath(relative_to_target).as_posix()
            entries.append((normalized, is_directory))
            if not is_directory:
                continue

            try:
                nested = sorted(entry.iterdir(), key=lambda item: item.name.lower(), reverse=True)
            except OSError:
                continue
            prefix = PurePosixPath(normalized)
            stack.extend((child, (prefix / child.name).as_posix()) for child in nested)

        return entries, False

    @staticmethod
    def relative_path(project: ProjectSpec, path: Path) -> str:
        return path.resolve(strict=False).relative_to(project.root.resolve(strict=False)).as_posix()
