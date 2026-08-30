from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import codemcp_bridge.project_registry as registry_module
from codemcp_bridge.project_registry import ProjectRegistry
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ServerSettings,
    StorageSettings,
    load_projects,
)


def _write_projects(path: Path, projects: dict[str, Path]) -> None:
    lines = ["# test registry"]
    for project_id, root in projects.items():
        lines.extend(
            [
                "",
                f"[projects.{project_id}]",
                f'root = "{root.as_posix()}"',
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects=load_projects(projects_path),
    )


def test_registry_refresh_installs_one_validated_snapshot(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"project_a": project_a})

    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    assert registry.generation == 1
    assert set(registry.snapshot()) == {"project_a"}

    _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})

    assert registry.refresh_if_changed() is True
    assert registry.generation == 2
    assert registry.last_reload_status == "ok"
    assert registry.last_reload_error is None
    assert set(registry.snapshot()) == {"project_a", "project_b"}
    assert registry.refresh_if_changed() is False
    assert registry.generation == 2


def test_registry_invalid_candidate_preserves_last_known_good_snapshot(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"project_a": project_a})

    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    projects_path.write_text("[projects.project_b\n", encoding="utf-8")

    assert registry.refresh_if_changed() is False
    assert registry.generation == 1
    assert registry.last_reload_status == "failed"
    assert registry.last_reload_error is not None
    assert set(registry.snapshot()) == {"project_a"}

    _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})

    assert registry.refresh_if_changed() is True
    assert registry.generation == 2
    assert registry.last_reload_status == "ok"
    assert registry.last_reload_error is None
    assert set(registry.snapshot()) == {"project_a", "project_b"}


def test_registry_rejects_candidate_that_changes_during_reload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_c = tmp_path / "project-c"
    project_a.mkdir()
    project_b.mkdir()
    project_c.mkdir()
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"project_a": project_a})
    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})

    original_load_projects = registry_module.load_projects

    def racing_load_projects(path: Path):
        candidate = original_load_projects(path)
        _write_projects(
            projects_path,
            {"project_a": project_a, "project_b": project_b, "project_c": project_c},
        )
        return candidate

    monkeypatch.setattr(registry_module, "load_projects", racing_load_projects)

    assert registry.refresh_if_changed() is False
    assert registry.generation == 1
    assert registry.last_reload_status == "failed"
    assert registry.last_reload_error == "project configuration changed during reload"
    assert set(registry.snapshot()) == {"project_a"}


def test_registry_concurrent_refresh_installs_one_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    projects_path = tmp_path / "projects.toml"
    _write_projects(projects_path, {"project_a": project_a})
    registry = ProjectRegistry(_settings(tmp_path, projects_path))
    _write_projects(projects_path, {"project_a": project_a, "project_b": project_b})

    original_load_projects = registry_module.load_projects
    call_count = 0
    counter_lock = threading.Lock()

    def counted_load_projects(path: Path):
        nonlocal call_count
        with counter_lock:
            call_count += 1
        time.sleep(0.02)
        return original_load_projects(path)

    monkeypatch.setattr(registry_module, "load_projects", counted_load_projects)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: registry.refresh_if_changed(), range(8)))

    assert results.count(True) == 1
    assert call_count == 1
    assert registry.generation == 2
    assert set(registry.snapshot()) == {"project_a", "project_b"}
