from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from codemcp_bridge.mcp_server import BridgeService
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    CommandSpec,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)


class _NoopAdapter:
    async def call(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("project_status must not call the codemcp adapter")

    def is_active(self, project_id: str) -> bool:
        return False

    async def close(self) -> None:
        return None


def _git(project: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=project, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_project_status_exposes_development_readiness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["python", "-m", "pytest"]\n',
        encoding="utf-8",
    )
    (project / "README.md").write_text("demo\n", encoding="utf-8")
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Readiness test")
    _git(project, "config", "user.email", "readiness@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: readiness fixture")

    command = CommandSpec(
        command_id="test",
        kind="test",
        argv=("python", "-m", "pytest"),
        timeout_seconds=30,
        approval="not-required",
    )
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={"test": command},
    )
    data_dir = tmp_path / "bridge-data"
    data_dir.mkdir()
    settings = BridgeSettings(
        repository_root=tmp_path,
        bridge_config_path=tmp_path / "bridge.toml",
        projects_config_path=tmp_path / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(data_dir, data_dir / "bridge.sqlite3", data_dir / "logs"),
        policy=PolicySettings(False, False, False, True, 4096, 16384, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects={"demo": spec},
    )
    service = BridgeService(settings, adapter=_NoopAdapter())
    try:
        payload = await service.project_status(None, "demo", None)
    finally:
        await service.close()

    assert payload["status"] == "succeeded"
    data = payload["data"]
    assert data["profile"] is None
    assert data["profile_source"] == "none"
    assert data["profile_resolved"] is False
    assert data["commands_resolved"] is True
    assert data["available_commands"] == ["test"]
    assert data["command_verification"] == {
        "matched": ["test"],
        "missing": [],
        "mismatched": [],
    }
    assert data["codemcp_config_ready"] is True
    assert data["development_ready"] is True
    assert data["issues"] == []
