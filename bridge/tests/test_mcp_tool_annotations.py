from __future__ import annotations

from codemcp_bridge.mcp_server import create_server
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)


def _settings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("develop",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={},
    )
    return BridgeSettings(
        repository_root=tmp_path,
        bridge_config_path=tmp_path / "bridge.toml",
        projects_config_path=tmp_path / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(
            tmp_path / ".local",
            tmp_path / ".local/db",
            tmp_path / ".local/logs",
        ),
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects={"demo": spec},
    )


def _annotation_payload(server, tool_name: str) -> dict[str, object]:
    tool = next(item for item in server._tool_manager.list_tools() if item.name == tool_name)
    assert tool.annotations is not None
    return tool.annotations.model_dump(by_alias=True, exclude_none=True)


def test_execution_tools_publish_closed_world_risk_annotations(tmp_path) -> None:
    server, _ = create_server(_settings(tmp_path))

    destructive = {
        "registered_command_run",
        "format_run",
        "test_run",
        "checkpoint_restore",
        "approval_confirm",
        "operation_reconcile",
    }
    non_destructive = {"checkpoint_create", "operation_cancel"}

    for tool_name in destructive | non_destructive:
        annotations = _annotation_payload(server, tool_name)
        assert annotations["read_only_hint"] is False
        assert annotations["idempotent_hint"] is True
        assert annotations["open_world_hint"] is False
        assert annotations["destructive_hint"] is (tool_name in destructive)
