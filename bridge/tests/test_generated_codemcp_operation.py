from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codemcp_bridge.command_runner import CommandRunResult
from codemcp_bridge.mcp_server import create_app, create_server
from codemcp_bridge.operation_service import request_hash
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    CommandSpec,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)
from codemcp_bridge.worker_manager import AdapterResult


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


class HeadMutatingRunner:
    def __init__(self) -> None:
        self.mutate_head = True
        self.calls = 0

    async def run(self, project: ProjectSpec, command: CommandSpec) -> CommandRunResult:
        self.calls += 1
        if self.mutate_head:
            _git(project.root, "commit", "--allow-empty", "-m", "unexpected command commit")
        return CommandRunResult(
            text=f"Code {command.kind} successful:\ntest passed\n",
            is_error=False,
            truncated=False,
        )


class ConfigCheckingEditAdapter:
    def __init__(self) -> None:
        self.saw_config = False

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, object],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        del timeout_seconds, mutation
        if subtool != "EditFile":
            return AdapterResult(f"fake {subtool}", False)

        self.saw_config = project.codemcp_config.is_file()
        target = Path(str(arguments["path"]))
        old_string = str(arguments["old_string"])
        new_string = str(arguments["new_string"])
        target.write_text(
            target.read_text(encoding="utf-8").replace(old_string, new_string, 1),
            encoding="utf-8",
        )
        relative = target.relative_to(project.root).as_posix()
        _git(project.root, "add", relative)
        _git(project.root, "commit", "--amend", "--no-edit")
        return AdapterResult("fake EditFile", False)

    def is_active(self, project_id: str) -> bool:
        del project_id
        return False

    async def close(self) -> None:
        return None


def _settings(
    project: Path,
    *,
    profile: str | None = None,
    profile_source: str = "none",
) -> BridgeSettings:
    command = CommandSpec(
        command_id="test",
        kind="test",
        argv=("python", "-c", "print('test passed')"),
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
        profile=profile,
        profile_source=profile_source,
    )
    data_dir = project.parent / ".local-command-operation"
    return BridgeSettings(
        repository_root=project.parent,
        bridge_config_path=project.parent / "bridge.toml",
        projects_config_path=project.parent / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(data_dir, data_dir / "bridge.sqlite3", data_dir / "logs"),
        policy=PolicySettings(False, False, False, True, 4096, 16_384, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 30, 60, 5),
        projects={"demo": spec},
    )


@pytest.mark.asyncio
async def test_root_only_file_edit_bypasses_codemcp_adapter_and_generated_config(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "generated-config-test")
    _git(project, "config", "user.email", "generated-config-test@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: root-only generated config fixture")

    adapter = ConfigCheckingEditAdapter()
    service = create_server(
        _settings(project, profile="python", profile_source="detected"),
        adapter,
    )[1]
    await service.start()
    session = service.sessions.create("demo")
    description = "replace root-only fixture text"
    result = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "notes.txt",
        "before",
        "after",
        description,
        "root-only-file-edit-1",
        request_hash(
            {
                "path": "notes.txt",
                "description": description,
                "old_string_digest": request_hash("before"),
                "new_string_digest": request_hash("after"),
            }
        ),
    )

    assert result["status"] == "succeeded"
    assert adapter.saw_config is False
    assert project.joinpath("codemcp.toml").exists() is False
    assert target.read_text(encoding="utf-8") == "after\n"
    assert _git(project, "status", "--porcelain") == ""
    await service.close()


@pytest.mark.asyncio
async def test_registered_command_head_change_blocks_project_until_reconcile(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["python", "-c", "print(\'test passed\')"]\n',
        encoding="utf-8",
    )
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "command-runner-test")
    _git(project, "config", "user.email", "command-runner-test@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: command runner operation fixture")

    service = create_app(_settings(project))[1]
    runner = HeadMutatingRunner()
    service.command_runner = runner
    await service.start()
    session = service.sessions.create("demo")

    first = await service.registered_command_run(
        None,
        "demo",
        session.session_id,
        "test",
        "head-mutation-1",
        request_hash({"command_id": "test"}),
    )
    assert first["status"] == "unknown"
    assert first["error"]["code"] == "UNKNOWN_SIDE_EFFECT"
    operation_id = first["operation_id"]
    assert service.operations.operation(operation_id).state == "unknown"
    assert _git(project, "status", "--porcelain") == ""

    blocked = await service.registered_command_run(
        None,
        "demo",
        session.session_id,
        "test",
        "head-mutation-blocked-1",
        request_hash({"command_id": "test"}),
    )
    assert blocked["error"]["code"] == "OPERATION_BLOCKED"
    assert blocked["error"]["details"]["operation_id"] == operation_id

    evidence = (
        "unexpected command commit confirmed; repository is clean and the changed HEAD is "
        "understood"
    )
    reconciled = await service.operation_reconcile(
        None,
        operation_id,
        session.session_id,
        "succeeded",
        evidence,
        "head-mutation-reconcile-1",
        request_hash(
            {
                "operation_id": operation_id,
                "decision": "succeeded",
                "evidence_digest": request_hash(evidence),
            }
        ),
    )
    assert reconciled["status"] == "succeeded"

    runner.mutate_head = False
    after = await service.registered_command_run(
        None,
        "demo",
        session.session_id,
        "test",
        "head-mutation-after-reconcile-1",
        request_hash({"command_id": "test"}),
    )
    assert after["status"] == "succeeded"
    assert runner.calls == 2
    assert _git(project, "status", "--porcelain") == ""

    await service.close()
