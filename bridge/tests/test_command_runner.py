from __future__ import annotations

import os
from pathlib import Path

from codemcp_bridge.command_runner import build_command_invocation
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    CommandSpec,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
    to_wsl_path,
)


def _settings(tmp_path: Path, project: ProjectSpec, worker_mode: str) -> BridgeSettings:
    return BridgeSettings(
        repository_root=tmp_path,
        bridge_config_path=tmp_path / "bridge.toml",
        projects_config_path=tmp_path / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(
            tmp_path / ".local", tmp_path / ".local/db", tmp_path / ".local/logs"
        ),
        policy=PolicySettings(False, False, False, True, 4096, 16_384, "per-project"),
        codemcp=CodemcpSettings(worker_mode, "Ubuntu", None, 30, 60, 5),
        projects={"demo": project},
    )


def _python_project(tmp_path: Path) -> tuple[ProjectSpec, CommandSpec]:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    command = CommandSpec(
        command_id="test",
        kind="test",
        argv=("python", "-m", "unittest", "discover", "-s", "tests", "-v"),
        timeout_seconds=900,
        approval="not-required",
    )
    project = ProjectSpec(
        project_id="demo",
        root=root,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=root / "codemcp.toml",
        commands={"test": command},
        profile="python",
        profile_source="detected",
    )
    return project, command


def test_wsl_python_src_test_uses_fixed_env_prefix_without_shell(tmp_path: Path) -> None:
    project, command = _python_project(tmp_path)
    invocation = build_command_invocation(
        _settings(tmp_path, project, "wsl2"),
        project,
        command,
        os_name="nt",
    )

    assert invocation.executable == "wsl.exe"
    assert invocation.cwd is None
    assert invocation.environment is None
    assert invocation.arguments == (
        "--distribution",
        "Ubuntu",
        "--cd",
        to_wsl_path(project.root),
        "--",
        "/usr/bin/env",
        "PYTHONPATH=src",
        *command.argv,
    )
    assert "-c" not in invocation.arguments


def test_local_python_src_test_prepends_src_to_pythonpath(tmp_path: Path) -> None:
    project, command = _python_project(tmp_path)
    invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        command,
        os_name="posix",
    )

    assert invocation.executable == "python"
    assert invocation.arguments == command.argv[1:]
    assert invocation.cwd == project.root
    assert invocation.environment is not None
    assert invocation.environment["PYTHONPATH"].split(os.pathsep)[0] == str(project.root / "src")


def test_local_windows_python_prefers_project_virtualenv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project, command = _python_project(tmp_path)
    venv_python = project.root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_bytes(b"")
    monkeypatch.setattr(
        "codemcp_bridge.command_runner.shutil.which",
        lambda executable: r"C:\System\python.exe" if executable == "python" else None,
    )

    invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        command,
        os_name="nt",
    )

    assert invocation.executable == str(venv_python.resolve())
    assert invocation.arguments == command.argv[1:]
    assert invocation.cwd == project.root


def test_local_windows_python_falls_back_to_py_launcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project, _ = _python_project(tmp_path)
    command = CommandSpec(
        command_id="doctor",
        kind="doctor",
        argv=("python", "--version"),
        timeout_seconds=60,
        approval="not-required",
    )
    monkeypatch.setattr(
        "codemcp_bridge.command_runner.shutil.which",
        lambda executable: r"C:\Windows\py.exe" if executable == "py" else None,
    )

    invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        command,
        os_name="nt",
    )

    assert invocation.executable == r"C:\Windows\py.exe"
    assert invocation.arguments == ("-3", "--version")
    assert invocation.cwd == project.root


def test_local_windows_resolves_fixed_executable_through_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project, _ = _python_project(tmp_path)
    command = CommandSpec(
        command_id="doctor",
        kind="doctor",
        argv=("mvn", "--version"),
        timeout_seconds=60,
        approval="not-required",
    )
    monkeypatch.setattr(
        "codemcp_bridge.command_runner.shutil.which",
        lambda executable: r"C:\Tools\Maven\bin\mvn.cmd" if executable == "mvn" else None,
    )

    invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        command,
        os_name="nt",
    )

    assert invocation.executable == r"C:\Tools\Maven\bin\mvn.cmd"
    assert invocation.arguments == ("--version",)
    assert invocation.cwd == project.root


def test_local_windows_migrates_legacy_wsl_self_repository_tools(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "bridge").mkdir(parents=True)
    (root / "bridge" / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    project = ProjectSpec(
        project_id="demo",
        root=root,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=root / "codemcp.toml",
        commands={},
    )
    legacy_prefix = f"{to_wsl_path(root)}/.local/bridge-venv-wsl/bin"

    python_command = CommandSpec(
        command_id="test",
        kind="test",
        argv=(f"{legacy_prefix}/python", "-m", "pytest", "-q"),
        timeout_seconds=900,
        approval="not-required",
    )
    python_invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        python_command,
        os_name="nt",
    )
    assert python_invocation.executable == "uv"
    assert python_invocation.arguments == (
        "run",
        "--project",
        str(root / "bridge"),
        "python",
        "-m",
        "pytest",
        "-q",
    )

    ruff_command = CommandSpec(
        command_id="format",
        kind="format",
        argv=(f"{legacy_prefix}/ruff", "format", "--check", "bridge/src"),
        timeout_seconds=300,
        approval="not-required",
    )
    ruff_invocation = build_command_invocation(
        _settings(tmp_path, project, "local"),
        project,
        ruff_command,
        os_name="nt",
    )
    assert ruff_invocation.executable == "uv"
    assert ruff_invocation.arguments == (
        "run",
        "--project",
        str(root / "bridge"),
        "ruff",
        "format",
        "--check",
        "bridge/src",
    )
