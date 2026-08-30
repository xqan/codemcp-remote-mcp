from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from codemcp_bridge.errors import BridgeError
from codemcp_bridge.git_guard import GitGuard
from codemcp_bridge.policy_engine import PolicyEngine
from codemcp_bridge.project_registry import ProjectRegistry
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    CommandSpec,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)


def _git(project: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=project, check=True, capture_output=True)


def _settings(project: Path) -> BridgeSettings:
    command = CommandSpec(
        command_id="format",
        kind="format",
        argv=("python", "-c", "print('format')"),
        timeout_seconds=30,
        approval="not-required",
    )
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={"format": command},
    )
    return BridgeSettings(
        repository_root=project.parent,
        bridge_config_path=project.parent / "bridge.toml",
        projects_config_path=project.parent / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(
            project.parent / ".local",
            project.parent / ".local/db",
            project.parent / ".local/logs",
        ),
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects={"demo": spec},
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "registered project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "hello.txt").write_text("hello\n", encoding="utf-8")
    (project / "codemcp.toml").write_text(
        '[commands.format]\ncommand = ["python", "-c", "print(\'format\')"]\n',
        encoding="utf-8",
    )
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase 2 test")
    _git(project, "config", "user.email", "phase2@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: initial project")
    return project


def _error_code(error: pytest.ExceptionInfo[BridgeError]) -> str:
    return error.value.code


def test_registry_rejects_unregistered_escape_and_sensitive_paths(git_project: Path) -> None:
    registry = ProjectRegistry(_settings(git_project))

    project, target, relative = registry.resolve_path("demo", "src/hello.txt")
    assert project.project_id == "demo"
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert relative == "src/hello.txt"

    with pytest.raises(BridgeError) as unregistered:
        registry.get("missing")
    assert _error_code(unregistered) == "PROJECT_NOT_ALLOWED"

    with pytest.raises(BridgeError) as escaped:
        registry.resolve_path("demo", "../outside.txt")
    assert _error_code(escaped) == "PATH_ESCAPE"

    for sensitive_path in (
        ".env",
        "local.env",
        "prod.env",
        "config/tunnel-profile.local.env",
        "secrets/private.key",
    ):
        with pytest.raises(BridgeError) as sensitive:
            registry.resolve_path("demo", sensitive_path)
        assert _error_code(sensitive) == "SENSITIVE_PATH"


def test_registry_rejects_symlink_components(git_project: Path) -> None:
    link = git_project / "src" / "linked.txt"
    try:
        link.symlink_to(git_project / "src" / "hello.txt")
    except OSError:
        pytest.skip("symlink creation is not permitted by this Windows account")

    with pytest.raises(BridgeError) as escaped:
        ProjectRegistry(_settings(git_project)).resolve_path("demo", "src/linked.txt")
    assert _error_code(escaped) == "PATH_ESCAPE"


@pytest.mark.asyncio
async def test_policy_rejects_dirty_workspace_and_command_drift(git_project: Path) -> None:
    settings = _settings(git_project)
    registry = ProjectRegistry(settings)
    policy = PolicyEngine(settings, registry, GitGuard())
    project = registry.get("demo")

    command = policy.command(project, "format", "format")
    assert command.argv == ("python", "-c", "print('format')")

    (git_project / "src" / "hello.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(BridgeError) as dirty:
        await policy.require_mutation_preconditions(project)
    assert _error_code(dirty) == "WORKSPACE_DIRTY"

    drifted = replace(
        settings,
        projects={
            "demo": replace(
                project,
                commands={"format": replace(command, argv=("python", "-c", "print('untrusted')"))},
            )
        },
    )
    drift_policy = PolicyEngine(drifted, ProjectRegistry(drifted), GitGuard())
    with pytest.raises(BridgeError) as drift:
        drift_policy.command(drifted.projects["demo"], "format", "format")
    assert _error_code(drift) == "COMMAND_NOT_ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("global_requires_clean", "project_requires_clean", "expect_rejection"),
    [
        (True, False, True),
        (False, True, True),
        (False, False, False),
    ],
)
async def test_clean_workspace_policy_requires_both_layers_to_opt_out(
    git_project: Path,
    global_requires_clean: bool,
    project_requires_clean: bool,
    expect_rejection: bool,
) -> None:
    settings = _settings(git_project)
    project = replace(settings.projects["demo"], require_clean_workspace=project_requires_clean)
    settings = replace(
        settings,
        policy=replace(settings.policy, require_clean_workspace=global_requires_clean),
        projects={"demo": project},
    )
    policy = PolicyEngine(settings, ProjectRegistry(settings), GitGuard())
    (git_project / "src" / "hello.txt").write_text("dirty\n", encoding="utf-8")

    if expect_rejection:
        with pytest.raises(BridgeError) as dirty:
            await policy.require_mutation_preconditions(project)
        assert _error_code(dirty) == "WORKSPACE_DIRTY"
    else:
        status = await policy.require_mutation_preconditions(project)
        assert status.dirty is True


@pytest.mark.asyncio
async def test_git_diff_rejects_sensitive_paths(git_project: Path) -> None:
    secret = git_project / "private.key"
    secret.write_text("private material\n", encoding="utf-8")
    _git(git_project, "add", "private.key")
    _git(git_project, "commit", "-m", "test: add sensitive fixture")
    secret.write_text("changed private material\n", encoding="utf-8")

    with pytest.raises(BridgeError) as raised:
        await GitGuard().diff(git_project)
    assert _error_code(raised) == "SENSITIVE_PATH"
