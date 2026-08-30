"""Configuration models for the local Bridge."""

from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .project_profiles import SUPPORTED_PROFILE_IDS
from .project_resolution import resolve_project_profile
from .security_defaults import (
    DEFAULT_ALLOWED_BRANCHES,
    DEFAULT_REQUIRE_CLEAN_WORKSPACE,
    default_command_approval,
    default_command_timeout_seconds,
)

PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SettingsError(ValueError):
    """Raised when Bridge configuration is unsafe or structurally invalid."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command_id: str
    kind: str
    argv: tuple[str, ...]
    timeout_seconds: float
    approval: str


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    project_id: str
    root: Path
    allowed_branches: tuple[str, ...]
    require_clean_workspace: bool
    codemcp_config: Path
    commands: dict[str, CommandSpec]
    profile: str | None = None
    profile_source: str = "none"


@dataclass(frozen=True, slots=True)
class ServerSettings:
    host: str
    port: int
    path: str
    transport: str


@dataclass(frozen=True, slots=True)
class StorageSettings:
    data_dir: Path
    sqlite_file: Path
    log_dir: Path


@dataclass(frozen=True, slots=True)
class PolicySettings:
    allow_arbitrary_paths: bool
    allow_arbitrary_commands: bool
    allow_model_calls: bool
    require_clean_workspace: bool
    max_file_bytes: int
    max_result_bytes: int
    mutation_lock: str
    approval_ttl_seconds: float = 300


@dataclass(frozen=True, slots=True)
class CodemcpSettings:
    worker_mode: str
    wsl_distribution: str
    wsl_python: str | None
    startup_timeout_seconds: float
    worker_timeout_seconds: float
    shutdown_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class BridgeSettings:
    repository_root: Path
    bridge_config_path: Path
    projects_config_path: Path
    server: ServerSettings
    storage: StorageSettings
    policy: PolicySettings
    codemcp: CodemcpSettings
    projects: dict[str, ProjectSpec]


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError(f"{name} must be a TOML table")
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise SettingsError(f"cannot read configuration: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"invalid TOML configuration: {path}: {exc}") from exc


def _resolve_path(base: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{name} must be a non-empty path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def _relative_config_path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{name} must be a non-empty relative path")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise SettingsError(f"{name} must be relative")
    path = Path(value)
    if any(part == ".." for part in path.parts):
        raise SettingsError(f"{name} must not contain '..'")
    return path


def _parse_command(command_id: str, raw: Any) -> CommandSpec:
    if not isinstance(raw, dict):
        raise SettingsError(f"commands.{command_id} must be a TOML table")
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise SettingsError(f"commands.{command_id}.argv must be a non-empty string list")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        raise SettingsError(f"commands.{command_id}.kind must be a non-empty string")
    default_approval = default_command_approval(kind)
    approval = raw.get("approval", default_approval)
    if approval not in {"not-required", "required"}:
        raise SettingsError(f"commands.{command_id}.approval must be 'not-required' or 'required'")
    if default_approval == "required" and approval != "required":
        raise SettingsError(
            f"commands.{command_id}.approval cannot disable required approval for kind {kind!r}"
        )
    timeout_seconds = raw.get("timeout_seconds", default_command_timeout_seconds(kind))
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or timeout_seconds <= 0
    ):
        raise SettingsError(f"commands.{command_id}.timeout_seconds must be positive")
    return CommandSpec(
        command_id=command_id,
        kind=kind,
        argv=tuple(argv),
        timeout_seconds=float(timeout_seconds),
        approval=approval,
    )


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _apply_profile_root_command_defaults(
    root: Path,
    profile_id: str | None,
    commands: dict[str, CommandSpec],
) -> None:
    if profile_id != "python":
        return
    test_script = root / "run_tests.sh"
    existing = commands.get("test")
    if existing is None or not _is_regular_file(test_script):
        return
    try:
        script = test_script.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeError):
        return

    unittest_command = "python -m unittest discover -s tests -v"
    lines = tuple(line.strip() for line in script.splitlines())
    uses_unittest = any(
        line == unittest_command
        or (line.startswith("PYTHONPATH=") and line.endswith(f" {unittest_command}"))
        for line in lines
    )
    if uses_unittest:
        argv = ("python", "-m", "unittest", "discover", "-s", "tests", "-v")
    elif "\r" not in test_script.read_text(encoding="utf-8", errors="ignore"):
        argv = ("/bin/sh", "./run_tests.sh")
    else:
        return

    commands["test"] = CommandSpec(
        command_id="test",
        kind="test",
        argv=argv,
        timeout_seconds=existing.timeout_seconds,
        approval=existing.approval,
    )


def _parse_projects(path: Path, base: Path) -> dict[str, ProjectSpec]:
    raw_projects = _as_mapping(_read_toml(path).get("projects", {}), "projects")
    projects: dict[str, ProjectSpec] = {}
    for project_id, raw_value in raw_projects.items():
        if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
            raise SettingsError(f"invalid project_id: {project_id!r}")
        raw = _as_mapping(raw_value, f"projects.{project_id}")
        root = _resolve_path(base, raw.get("root"), f"projects.{project_id}.root")
        raw_branches = raw.get("allowed_branches", list(DEFAULT_ALLOWED_BRANCHES))
        if (
            not isinstance(raw_branches, list)
            or not raw_branches
            or not all(isinstance(item, str) and item for item in raw_branches)
        ):
            raise SettingsError(
                f"projects.{project_id}.allowed_branches must be a non-empty string list"
            )
        codemcp_config_name = _relative_config_path(
            raw.get("codemcp_config", "codemcp.toml"),
            f"projects.{project_id}.codemcp_config",
        )
        explicit_profile = raw.get("profile")
        if explicit_profile is not None:
            if not isinstance(explicit_profile, str) or not PROFILE_ID_PATTERN.fullmatch(
                explicit_profile
            ):
                raise SettingsError(
                    f"projects.{project_id}.profile must be a lowercase profile identifier"
                )
            if explicit_profile not in SUPPORTED_PROFILE_IDS:
                raise SettingsError(
                    "projects."
                    f"{project_id}.profile is not a supported built-in profile: {explicit_profile}"
                )

        resolution = resolve_project_profile(root, explicit_profile)
        profile_commands: dict[str, CommandSpec] = {}
        if resolution.profile is not None:
            profile_commands = {
                command_id: CommandSpec(
                    command_id=command.command_id,
                    kind=command.kind,
                    argv=command.argv,
                    timeout_seconds=command.timeout_seconds,
                    approval=command.approval,
                )
                for command_id, command in resolution.profile.commands.items()
            }

        _apply_profile_root_command_defaults(root, resolution.profile_id, profile_commands)

        commands_raw = _as_mapping(raw.get("commands", {}), f"projects.{project_id}.commands")
        explicit_commands = {
            command_id: _parse_command(command_id, command_raw)
            for command_id, command_raw in commands_raw.items()
        }
        commands = {**profile_commands, **explicit_commands}
        require_clean_workspace = raw.get(
            "require_clean_workspace", DEFAULT_REQUIRE_CLEAN_WORKSPACE
        )
        if not isinstance(require_clean_workspace, bool):
            raise SettingsError(f"projects.{project_id}.require_clean_workspace must be boolean")
        projects[project_id] = ProjectSpec(
            project_id=project_id,
            root=root,
            allowed_branches=tuple(raw_branches),
            require_clean_workspace=require_clean_workspace,
            codemcp_config=root / codemcp_config_name,
            commands=commands,
            profile=resolution.profile_id,
            profile_source=resolution.source,
        )
    return projects


def load_projects(projects_config_path: Path | str) -> dict[str, ProjectSpec]:
    """Load and validate only the project-authorization configuration."""

    projects_path = Path(projects_config_path).expanduser().resolve(strict=False)
    return _parse_projects(projects_path, projects_path.parent)


def load_settings(
    bridge_config_path: Path | str,
    projects_config_path: Path | str,
) -> BridgeSettings:
    """Load and validate the two explicit Bridge configuration files."""

    bridge_path = Path(bridge_config_path).expanduser().resolve(strict=False)
    projects_path = Path(projects_config_path).expanduser().resolve(strict=False)
    repository_root = bridge_path.parent.parent
    raw_bridge = _read_toml(bridge_path)
    server_raw = _as_mapping(raw_bridge.get("server", {}), "server")
    storage_raw = _as_mapping(raw_bridge.get("storage", {}), "storage")
    policy_raw = _as_mapping(raw_bridge.get("policy", {}), "policy")
    codemcp_raw = _as_mapping(raw_bridge.get("codemcp", {}), "codemcp")

    host = server_raw.get("host", "127.0.0.1")
    if host != "127.0.0.1":
        raise SettingsError("server.host must be 127.0.0.1")
    path = server_raw.get("path", "/mcp")
    if not isinstance(path, str) or not path.startswith("/"):
        raise SettingsError("server.path must start with '/'")
    transport = server_raw.get("transport", "streamable-http")
    if transport != "streamable-http":
        raise SettingsError("server.transport must be streamable-http in Phase 4")
    port = server_raw.get("port", 46200)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SettingsError("server.port must be between 1 and 65535")

    data_dir = _resolve_path(
        repository_root,
        storage_raw.get("data_dir", ".local"),
        "storage.data_dir",
    )
    sqlite_file = _resolve_path(
        repository_root,
        storage_raw.get("sqlite_file", ".local/bridge.sqlite3"),
        "storage.sqlite_file",
    )
    log_dir = _resolve_path(
        repository_root,
        storage_raw.get("log_dir", ".local/logs"),
        "storage.log_dir",
    )

    bool_keys = (
        "allow_arbitrary_paths",
        "allow_arbitrary_commands",
        "allow_model_calls",
        "require_clean_workspace",
    )
    for key in bool_keys:
        if not isinstance(policy_raw.get(key), bool):
            raise SettingsError(f"policy.{key} must be boolean")
    if any(policy_raw[key] for key in bool_keys[:3]):
        raise SettingsError(
            "policy.allow_arbitrary_paths, policy.allow_arbitrary_commands, and "
            "policy.allow_model_calls must remain false in Phase 4"
        )
    if policy_raw.get("mutation_lock", "per-project") != "per-project":
        raise SettingsError("policy.mutation_lock must be per-project in Phase 4")
    approval_ttl_seconds = policy_raw.get("approval_ttl_seconds", 300)
    if not isinstance(approval_ttl_seconds, int | float) or approval_ttl_seconds <= 0:
        raise SettingsError("policy.approval_ttl_seconds must be positive")
    max_file_bytes = policy_raw.get("max_file_bytes", 1_048_576)
    max_result_bytes = policy_raw.get("max_result_bytes", 262_144)
    if not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
        raise SettingsError("policy.max_file_bytes must be positive")
    if not isinstance(max_result_bytes, int) or max_result_bytes <= 0:
        raise SettingsError("policy.max_result_bytes must be positive")

    worker_mode = codemcp_raw.get("worker_mode", "local")
    if worker_mode not in {"wsl2", "local"}:
        raise SettingsError("codemcp.worker_mode must be 'wsl2' or 'local'")
    wsl_distribution = codemcp_raw.get("wsl_distribution", "Ubuntu")
    if not isinstance(wsl_distribution, str) or not wsl_distribution:
        raise SettingsError("codemcp.wsl_distribution must be a non-empty string")
    wsl_python = codemcp_raw.get("wsl_python")
    if wsl_python == "":
        wsl_python = None
    if wsl_python is not None and not isinstance(wsl_python, str):
        raise SettingsError("codemcp.wsl_python must be a string or empty")

    def positive_float(key: str, default: float) -> float:
        value = codemcp_raw.get(key, default)
        if not isinstance(value, int | float) or value <= 0:
            raise SettingsError(f"codemcp.{key} must be positive")
        return float(value)

    return BridgeSettings(
        repository_root=repository_root,
        bridge_config_path=bridge_path,
        projects_config_path=projects_path,
        server=ServerSettings(
            host=host,
            port=port,
            path=path.rstrip("/") or "/",
            transport=transport,
        ),
        storage=StorageSettings(data_dir=data_dir, sqlite_file=sqlite_file, log_dir=log_dir),
        policy=PolicySettings(
            allow_arbitrary_paths=policy_raw["allow_arbitrary_paths"],
            allow_arbitrary_commands=policy_raw["allow_arbitrary_commands"],
            allow_model_calls=policy_raw["allow_model_calls"],
            require_clean_workspace=policy_raw["require_clean_workspace"],
            max_file_bytes=max_file_bytes,
            max_result_bytes=max_result_bytes,
            mutation_lock=str(policy_raw.get("mutation_lock", "per-project")),
            approval_ttl_seconds=float(approval_ttl_seconds),
        ),
        codemcp=CodemcpSettings(
            worker_mode=worker_mode,
            wsl_distribution=wsl_distribution,
            wsl_python=wsl_python,
            startup_timeout_seconds=positive_float("startup_timeout_seconds", 30),
            worker_timeout_seconds=positive_float("worker_timeout_seconds", 60),
            shutdown_timeout_seconds=positive_float("shutdown_timeout_seconds", 5),
        ),
        projects=load_projects(projects_path),
    )


def to_wsl_path(path: Path) -> str:
    """Map an absolute Windows path to the standard WSL mount path."""

    value = str(path)
    drive, tail = PureWindowsPath(value).drive, PureWindowsPath(value).root
    if drive and drive[0].isalpha():
        remainder = value[len(drive) :].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive[0].lower()}/{remainder}"
    if value.startswith("/"):
        return value
    if tail:
        return value.replace("\\", "/")
    return value.replace("\\", "/")


def normalize_relative_path(value: str) -> str:
    """Normalize a user path without resolving it against the filesystem."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise SettingsError("path must be a non-empty string")
    normalized = value.replace("\\", "/")
    pure_windows = PureWindowsPath(value)
    if normalized.startswith("/") or pure_windows.is_absolute() or pure_windows.drive:
        raise SettingsError("path must be relative")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise SettingsError("path must not escape the project root")
    return "/".join(parts) or "."
