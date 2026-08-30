from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from codemcp_bridge.mcp_server import BridgeService
from codemcp_bridge.project_registry import ProjectRegistry
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
    load_projects,
)
from codemcp_bridge.worker_manager import AdapterResult


def _git(project: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_project(path: Path) -> Path:
    path.mkdir()
    (path / "README.md").write_text("# hot reload test\n", encoding="utf-8")
    (path / "codemcp.toml").write_text("", encoding="utf-8")
    _git(path, "init", "-b", "develop")
    _git(path, "config", "user.name", "Project registry hot reload test")
    _git(path, "config", "user.email", "registry-hot-reload@example.invalid")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "test: initial project")
    return path


def _write_projects(
    path: Path,
    projects: dict[str, Path],
    *,
    allowed_branches: dict[str, tuple[str, ...]] | None = None,
) -> None:
    lines = ["# local CLI-style project registry"]
    for project_id, root in projects.items():
        lines.extend(["", f"[projects.{project_id}]", f'root = "{root.as_posix()}"'])
        branches = None if allowed_branches is None else allowed_branches.get(project_id)
        if branches is not None:
            encoded = ", ".join(f'"{branch}"' for branch in branches)
            lines.append(f"allowed_branches = [{encoded}]")
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def _settings(tmp_path: Path, projects_path: Path) -> BridgeSettings:
    return BridgeSettings(
        repository_root=tmp_path,
        bridge_config_path=tmp_path / "bridge.toml",
        projects_config_path=projects_path,
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(
            tmp_path / "data",
            tmp_path / "data" / "bridge.sqlite3",
            tmp_path / "logs",
        ),
        policy=PolicySettings(False, False, False, True, 1024 * 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects=load_projects(projects_path),
    )


class _FakeAdapter:
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        del project, subtool, arguments, timeout_seconds, mutation
        return AdapterResult("unused", False)

    def is_active(self, project_id: str) -> bool:
        del project_id
        return False

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_running_bridge_adds_and_removes_projects_without_restart(tmp_path: Path) -> None:
    project_a = _git_project(tmp_path / "project-a")
    project_b = _git_project(tmp_path / "project-b")
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"project_a": project_a})

    service = BridgeService(_settings(tmp_path, projects_path), _FakeAdapter())
    try:
        first = await service.project_open(None, "project_a")
        assert first["status"] == "succeeded"

        _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})
        added = await service.project_open(None, "project_b")

        assert added["status"] == "succeeded"
        session_id = added["data"]["session_id"]
        assert service.registry.generation == 2
        health = await service.health()
        assert health["projects_registered"] == 2
        assert health["project_registry"] == {
            "generation": 2,
            "reload_status": "ok",
            "last_reload_error": None,
        }
        assert "project_a" not in str(health["project_registry"])
        assert str(project_a) not in str(health["project_registry"])

        _write_projects(projects_path, {"project_a": project_a})
        removed = await service.project_status(None, "project_b", session_id)

        assert removed["status"] == "rejected"
        assert removed["error"]["code"] == "PROJECT_NOT_ALLOWED"
        blocked = service.database.get_session(session_id)
        assert blocked is not None
        assert blocked.status == "blocked"
        assert blocked.reason == "project_removed"

        _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})
        reopened = await service.project_open(None, "project_b")
        assert reopened["status"] == "succeeded"

        old_session = await service.project_status(None, "project_b", session_id)
        assert old_session["status"] == "rejected"
        assert old_session["error"]["code"] == "SESSION_NOT_FOUND"
    finally:
        await service.close()


def test_registry_rejects_root_redirect_until_removal_is_observed(tmp_path: Path) -> None:
    project_a = _git_project(tmp_path / "project-a")
    project_b = _git_project(tmp_path / "project-b")
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"demo": project_a})

    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    _write_projects(projects_path, {"demo": project_b})

    assert registry.refresh_if_changed() is False
    assert registry.get("demo").root == project_a.resolve()
    assert registry.last_reload_status == "failed"
    assert registry.last_reload_error == "project root change requires remove then add: demo"
    assert registry.last_reload_error_code == "project_root_change_requires_remove_add"

    _write_projects(projects_path, {})
    assert registry.refresh_if_changed() is True
    assert registry.snapshot() == {}

    _write_projects(projects_path, {"demo": project_b})
    assert registry.refresh_if_changed() is True
    assert registry.get("demo").root == project_b.resolve()


def test_project_policy_changes_apply_to_next_snapshot(tmp_path: Path) -> None:
    project = _git_project(tmp_path / "project")
    projects_path = tmp_path / "projects.toml"
    _write_projects(
        projects_path,
        {"demo": project},
        allowed_branches={"demo": ("main",)},
    )

    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    assert registry.get("demo").allowed_branches == ("main",)

    _write_projects(
        projects_path,
        {"demo": project},
        allowed_branches={"demo": ("release/*",)},
    )

    assert registry.refresh_if_changed() is True
    assert registry.get("demo").allowed_branches == ("release/*",)
