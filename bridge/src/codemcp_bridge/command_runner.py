"""Execute fixed registered project commands without shell or codemcp Git side effects."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .settings import BridgeSettings, CommandSpec, ProjectSpec, to_wsl_path


@dataclass(frozen=True, slots=True)
class CommandRunResult:
    text: str
    is_error: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    executable: str
    arguments: tuple[str, ...]
    cwd: Path | None
    environment: dict[str, str] | None = None


def _migrate_legacy_wsl_bridge_command(
    project: ProjectSpec,
    argv: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Translate the repository's pre-native WSL tool paths to uv on Windows."""

    if not argv or not (project.root / "bridge" / "pyproject.toml").is_file():
        return None
    executable = argv[0].replace("\\", "/")
    legacy_prefix = f"{to_wsl_path(project.root).rstrip('/')}/.local/bridge-venv-wsl/bin/"
    if executable == legacy_prefix + "python":
        return ("uv", "run", "--project", str(project.root / "bridge"), "python", *argv[1:])
    if executable == legacy_prefix + "ruff":
        return ("uv", "run", "--project", str(project.root / "bridge"), "ruff", *argv[1:])
    return None


def build_command_invocation(
    settings: BridgeSettings,
    project: ProjectSpec,
    command: CommandSpec,
    *,
    os_name: str | None = None,
) -> CommandInvocation:
    """Build one exact argv invocation; never route through a shell."""

    host_os = os.name if os_name is None else os_name
    src_path = project.root / "src"
    python_src_test = (
        project.profile == "python"
        and command.command_id == "test"
        and src_path.is_dir()
        and not src_path.is_symlink()
    )
    if settings.codemcp.worker_mode == "wsl2" and host_os == "nt":
        worker_argv = command.argv
        if python_src_test:
            worker_argv = ("/usr/bin/env", "PYTHONPATH=src", *command.argv)
        return CommandInvocation(
            executable="wsl.exe",
            arguments=(
                "--distribution",
                settings.codemcp.wsl_distribution,
                "--cd",
                to_wsl_path(project.root),
                "--",
                *worker_argv,
            ),
            cwd=None,
        )

    local_argv = command.argv
    if host_os == "nt":
        migrated_argv = _migrate_legacy_wsl_bridge_command(project, command.argv)
        if migrated_argv is not None:
            local_argv = migrated_argv
        elif (
            project.profile == "python"
            and local_argv
            and local_argv[0].casefold()
            in {
                "python",
                "python.exe",
            }
        ):
            venv_python = project.root / ".venv" / "Scripts" / "python.exe"
            if venv_python.is_file() and not venv_python.is_symlink():
                local_argv = (str(venv_python.resolve()), *local_argv[1:])
            else:
                resolved_python = shutil.which(local_argv[0])
                if resolved_python is not None:
                    local_argv = (resolved_python, *local_argv[1:])
                else:
                    py_launcher = shutil.which("py")
                    if py_launcher is not None:
                        local_argv = (py_launcher, "-3", *local_argv[1:])
        else:
            resolved_executable = shutil.which(local_argv[0])
            if resolved_executable is not None:
                local_argv = (resolved_executable, *local_argv[1:])

    environment: dict[str, str] | None = None
    if python_src_test:
        environment = dict(os.environ)
        existing_pythonpath = environment.get("PYTHONPATH")
        src_value = str(src_path)
        environment["PYTHONPATH"] = (
            src_value
            if not existing_pythonpath
            else f"{src_value}{os.pathsep}{existing_pythonpath}"
        )
    return CommandInvocation(
        executable=local_argv[0],
        arguments=local_argv[1:],
        cwd=project.root,
        environment=environment,
    )


def _truncate_bytes(value: bytes, max_bytes: int) -> tuple[str, bool]:
    if len(value) <= max_bytes:
        return value.decode("utf-8", errors="replace"), False
    return value[:max_bytes].decode("utf-8", errors="ignore"), True


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


class RegisteredCommandRunner:
    """Run only the fixed argv already resolved by Bridge policy."""

    def __init__(self, settings: BridgeSettings):
        self._settings = settings
        self._max_output_bytes = settings.policy.max_result_bytes

    async def run(self, project: ProjectSpec, command: CommandSpec) -> CommandRunResult:
        invocation = build_command_invocation(self._settings, project, command)
        process_kwargs: dict[str, object] = {}
        if os.name != "nt":
            process_kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                invocation.executable,
                *invocation.arguments,
                cwd=invocation.cwd,
                env=invocation.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **process_kwargs,
            )
        except OSError as exc:
            return CommandRunResult(
                text=f"Error: {command.kind.title()} command could not be started: {exc}",
                is_error=True,
                truncated=False,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=command.timeout_seconds,
            )
        except TimeoutError:
            await _terminate_process(process)
            return CommandRunResult(
                text=(
                    f"Error: {command.kind.title()} command timed out after "
                    f"{command.timeout_seconds:g}s"
                ),
                is_error=True,
                truncated=False,
            )

        stdout, stdout_truncated = _truncate_bytes(stdout_bytes, self._max_output_bytes)
        remaining = max(0, self._max_output_bytes - len(stdout.encode("utf-8")))
        stderr, stderr_truncated = _truncate_bytes(stderr_bytes, remaining)
        truncated = stdout_truncated or stderr_truncated

        if process.returncode != 0:
            stdout_info = f"STDOUT:\n{stdout}" if stdout else "STDOUT: <empty>"
            stderr_info = f"STDERR:\n{stderr}" if stderr else "STDERR: <empty>"
            return CommandRunResult(
                text=(
                    f"Error: {command.kind.title()} command failed with exit code "
                    f"{process.returncode}:\n{stdout_info}\n{stderr_info}"
                ),
                is_error=True,
                truncated=truncated,
            )

        output = stdout
        if stderr:
            output = f"{output}\nSTDERR:\n{stderr}" if output else f"STDERR:\n{stderr}"
        return CommandRunResult(
            text=f"Code {command.kind} successful:\n{output}",
            is_error=False,
            truncated=truncated,
        )
