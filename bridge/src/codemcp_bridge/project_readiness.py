"""Read-only development readiness diagnostics for registered projects."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .generated_codemcp import can_generate_codemcp_config
from .project_detection import detect_project_profile
from .settings import ProjectSpec


@dataclass(frozen=True, slots=True)
class DevelopmentReadiness:
    """Explain whether a resolved project can safely run its development workflow."""

    profile_id: str | None
    profile_source: str
    detection_candidates: tuple[str, ...]
    detection_ambiguous: bool
    available_commands: tuple[str, ...]
    matched_commands: tuple[str, ...]
    missing_commands: tuple[str, ...]
    mismatched_commands: tuple[str, ...]
    codemcp_config_exists: bool
    codemcp_config_valid: bool
    codemcp_config_source: str
    codemcp_config_ready: bool
    development_ready: bool
    issues: tuple[str, ...]


def _load_codemcp_commands(path: Path) -> tuple[bool, bool, dict[str, object]]:
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        return False, False, {}
    except (OSError, tomllib.TOMLDecodeError):
        return True, False, {}

    commands = config.get("commands", {})
    if not isinstance(commands, dict):
        return True, False, {}
    return True, True, commands


def inspect_development_readiness(project: ProjectSpec) -> DevelopmentReadiness:
    """Inspect project metadata and codemcp command alignment without executing commands."""

    detection = detect_project_profile(project.root)
    available_commands = tuple(sorted(project.commands))
    config_exists, config_valid, configured_commands = _load_codemcp_commands(
        project.codemcp_config
    )

    generated_config = not config_exists and can_generate_codemcp_config(project)
    matched: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    if generated_config:
        matched.extend(available_commands)
    elif config_valid:
        for command_id in available_commands:
            configured = configured_commands.get(command_id)
            if configured is None:
                missing.append(command_id)
                continue
            if not isinstance(configured, dict):
                mismatched.append(command_id)
                continue
            configured_argv = configured.get("command")
            if configured_argv == list(project.commands[command_id].argv):
                matched.append(command_id)
            else:
                mismatched.append(command_id)

    quality_gate_available = any(
        command.kind in {"test", "verify"} for command in project.commands.values()
    )
    issues: list[str] = []
    if detection.ambiguous and project.profile_source == "none":
        issues.append("project metadata is ambiguous; set an explicit profile")
    if not available_commands:
        issues.append("no development commands were resolved")
    if available_commands and not quality_gate_available:
        issues.append("no test or verify command is available")

    if not config_exists and not generated_config:
        issues.append("codemcp configuration file is missing")
    elif config_exists and not config_valid:
        issues.append("codemcp configuration file is invalid")
    elif config_exists:
        if missing:
            issues.append("codemcp configuration is missing commands: " + ", ".join(missing))
        if mismatched:
            issues.append(
                "codemcp command argv does not match resolved commands: " + ", ".join(mismatched)
            )

    if generated_config:
        codemcp_config_source = "generated"
    elif config_exists and config_valid:
        codemcp_config_source = "project"
    elif config_exists:
        codemcp_config_source = "invalid"
    else:
        codemcp_config_source = "missing"

    codemcp_config_ready = generated_config or (
        config_exists and config_valid and not missing and not mismatched
    )
    development_ready = (
        bool(available_commands)
        and quality_gate_available
        and codemcp_config_ready
        and not (detection.ambiguous and project.profile_source == "none")
    )

    return DevelopmentReadiness(
        profile_id=project.profile,
        profile_source=project.profile_source,
        detection_candidates=detection.candidates,
        detection_ambiguous=detection.ambiguous,
        available_commands=available_commands,
        matched_commands=tuple(matched),
        missing_commands=tuple(missing),
        mismatched_commands=tuple(mismatched),
        codemcp_config_exists=config_exists,
        codemcp_config_valid=config_valid,
        codemcp_config_source=codemcp_config_source,
        codemcp_config_ready=codemcp_config_ready,
        development_ready=development_ready,
        issues=tuple(issues),
    )
