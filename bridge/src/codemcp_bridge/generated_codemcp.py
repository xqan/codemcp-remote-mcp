"""Bridge-owned ephemeral codemcp.toml materialization for resolved profiles."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import BridgeError
from .settings import CommandSpec, ProjectSpec

GENERATED_CONFIG_NAME = "codemcp.toml"
GENERATED_HEADER = "# Generated temporarily by codemcp Bridge from the resolved project profile."


@dataclass(frozen=True, slots=True)
class GeneratedConfigLease:
    generated: bool
    path: Path


def can_generate_codemcp_config(project: ProjectSpec) -> bool:
    """Return whether Bridge may safely synthesize the default codemcp command catalog."""

    return (
        project.profile is not None
        and project.profile != "generic"
        and project.profile_source in {"detected", "explicit"}
        and bool(project.commands)
        and project.codemcp_config == project.root / GENERATED_CONFIG_NAME
    )


def render_generated_codemcp_config(project: ProjectSpec) -> str:
    """Render a deterministic codemcp.toml from the already-resolved fixed command catalog."""

    if not can_generate_codemcp_config(project):
        raise ValueError("project is not eligible for generated codemcp configuration")

    lines = [GENERATED_HEADER, ""]
    for command_id in sorted(project.commands):
        command = project.commands[command_id]
        quoted_id = json.dumps(command_id, ensure_ascii=False)
        quoted_argv = ", ".join(json.dumps(part, ensure_ascii=False) for part in command.argv)
        quoted_doc = json.dumps(f"Bridge-resolved {command.kind} command", ensure_ascii=False)
        lines.extend(
            (
                f"[commands.{quoted_id}]",
                f"command = [{quoted_argv}]",
                f"doc = {quoted_doc}",
                "",
            )
        )
    return "\n".join(lines)


def generated_config_sha256(project: ProjectSpec) -> str:
    return hashlib.sha256(render_generated_codemcp_config(project).encode("utf-8")).hexdigest()


def _verify_existing_config(path: Path, command: CommandSpec | None) -> None:
    """Revalidate an existing config at the materialization boundary to close TOCTOU gaps."""

    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise BridgeError(
                "COMMAND_NOT_ALLOWED",
                "project codemcp configuration must be a regular file",
                {"path": GENERATED_CONFIG_NAME},
            )
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except BridgeError:
        raise
    except (OSError, tomllib.TOMLDecodeError) as exc:
        details = {"path": GENERATED_CONFIG_NAME}
        if command is not None:
            details["command_id"] = command.command_id
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "project codemcp configuration cannot be verified during operation preparation",
            details,
        ) from exc

    if command is None:
        return

    commands = config.get("commands", {})
    configured = commands.get(command.command_id, {}) if isinstance(commands, dict) else {}
    configured_argv = configured.get("command") if isinstance(configured, dict) else None
    if configured_argv != list(command.argv):
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "project codemcp configuration changed before command execution",
            {"command_id": command.command_id},
        )


@contextmanager
def materialize_generated_codemcp_config(
    project: ProjectSpec,
    command: CommandSpec | None = None,
) -> Iterator[GeneratedConfigLease]:
    """Prepare an authoritative or generated config immediately before one codemcp operation."""

    path = project.codemcp_config
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "project codemcp configuration cannot be inspected",
            {"path": GENERATED_CONFIG_NAME},
        ) from exc

    if metadata is not None:
        _verify_existing_config(path, command)
        yield GeneratedConfigLease(False, path)
        return

    if not can_generate_codemcp_config(project):
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "project codemcp configuration is missing and cannot be generated",
        )

    content = render_generated_codemcp_config(project)
    expected_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "project codemcp configuration appeared during command preparation",
            {"path": GENERATED_CONFIG_NAME},
        ) from exc
    except OSError as exc:
        raise BridgeError(
            "COMMAND_NOT_ALLOWED",
            "generated codemcp configuration could not be materialized",
            {"path": GENERATED_CONFIG_NAME},
        ) from exc

    try:
        yield GeneratedConfigLease(True, path)
    finally:
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "generated codemcp configuration disappeared before cleanup",
                {"path": GENERATED_CONFIG_NAME},
                status="unknown",
            ) from exc
        current_digest = hashlib.sha256(current).hexdigest()
        if current_digest != expected_digest:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "generated codemcp configuration changed during command execution",
                {"path": GENERATED_CONFIG_NAME},
                status="unknown",
            )
        try:
            path.unlink()
        except OSError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "generated codemcp configuration could not be removed",
                {"path": GENERATED_CONFIG_NAME},
                status="unknown",
            ) from exc
