from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codemcp_bridge.errors import BridgeError, error_payload
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


def _settings(project: Path) -> BridgeSettings:
    format_command = CommandSpec(
        command_id="format",
        kind="format",
        argv=("python", "-c", "print('format')"),
        timeout_seconds=30,
        approval="required",
    )
    build_command = CommandSpec(
        command_id="build",
        kind="build",
        argv=("python", "-c", "print('build')"),
        timeout_seconds=30,
        approval="not-required",
    )
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={"format": format_command, "build": build_command},
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


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        del project, timeout_seconds, mutation
        self.calls.append((subtool, arguments))
        path = arguments.get("path")
        if subtool == "ReadFile":
            return AdapterResult(f"fake read: {Path(path).name}", False)
        return AdapterResult(f"fake {subtool}", False)

    def is_active(self, project_id: str) -> bool:
        del project_id
        return False

    async def close(self) -> None:
        return None


class CancellingEditAdapter(FakeAdapter):
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        if subtool == "EditFile":
            raise asyncio.CancelledError
        return await super().call(
            project,
            subtool,
            arguments,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )


class SlowReadAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        if subtool == "ReadFile":
            self.started.set()
            await asyncio.sleep(0.05)
        return await super().call(
            project,
            subtool,
            arguments,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )


class SearchAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.search_paths: list[Path] = []

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        if subtool == "Grep":
            self.search_paths.append(Path(arguments["path"]))
            return AdapterResult(
                "\n".join(
                    [
                        "Found 3 files",
                        str(project.root / "src" / "hello.txt"),
                        str(project.root / "secrets" / "private.key"),
                        str(project.root / "local.env"),
                    ]
                ),
                False,
            )
        return await super().call(
            project,
            subtool,
            arguments,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )


class WriteAdapter(FakeAdapter):
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        if subtool == "WriteFile":
            self.calls.append((subtool, arguments))
            target = Path(arguments["path"])
            target.write_text(arguments["content"], encoding="utf-8")
            relative = target.relative_to(project.root).as_posix()
            _git(project.root, "add", relative)
            _git(project.root, "commit", "--amend", "--no-edit")
            return AdapterResult("fake WriteFile", False)
        return await super().call(
            project,
            subtool,
            arguments,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )


class MultiFileEditAdapter(FakeAdapter):
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        if subtool == "EditFile":
            target = Path(arguments["path"])
            target.write_text(
                target.read_text(encoding="utf-8").replace(
                    arguments["old_string"], arguments["new_string"], 1
                ),
                encoding="utf-8",
            )
            (project.root / "src" / "notes.txt").write_text(
                "unexpected side effect\n", encoding="utf-8"
            )
            return AdapterResult("fake EditFile", False)
        return await super().call(
            project,
            subtool,
            arguments,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )


def _payload(result: Any) -> dict[str, Any]:
    if result.structuredContent:
        return result.structuredContent
    text_blocks = [block.text for block in result.content if hasattr(block, "text")]
    return json.loads("\n".join(text_blocks))


def _file_edit_input(
    path: str, old_string: str, new_string: str, description: str
) -> dict[str, str]:
    return {
        "path": path,
        "description": description,
        "old_string_digest": request_hash(old_string),
        "new_string_digest": request_hash(new_string),
    }


def _file_create_input(path: str, content: str, description: str) -> dict[str, str]:
    return {
        "path": path,
        "description": description,
        "content_digest": request_hash(content),
    }


def _file_write_input(
    path: str,
    content: str,
    expected_sha256: str,
    description: str,
) -> dict[str, str]:
    return {
        "path": path,
        "description": description,
        "expected_sha256": expected_sha256.lower(),
        "content_digest": request_hash(content),
    }


def _file_move_input(
    source_path: str,
    destination_path: str,
    description: str,
) -> dict[str, str]:
    return {
        "source_path": source_path,
        "destination_path": destination_path,
        "description": description,
    }


def _file_delete_input(path: str, description: str) -> dict[str, str]:
    return {
        "path": path,
        "description": description,
    }


def _directory_create_input(path: str, description: str) -> dict[str, str]:
    return {
        "path": path,
        "description": description,
    }


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "phase2 project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "hello.txt").write_bytes(b"hello\n")
    (project / "src" / "binary.bin").write_bytes(b"header\x00binary\n")
    (project / "src" / "large.txt").write_text("x" * 1025, encoding="utf-8")
    (project / "codemcp.toml").write_text(
        '[commands.format]\ncommand = ["python", "-c", "print(\'format\')"]\n'
        "\n"
        '[commands.build]\ncommand = ["python", "-c", "print(\'build\')"]\n',
        encoding="utf-8",
    )
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase 2 server test")
    _git(project, "config", "user.email", "phase2-server@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: initial project")
    return project


@pytest.mark.asyncio
async def test_local_mcp_contract_and_policy_rejections(
    git_project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    adapter = FakeAdapter()
    app, service = create_app(_settings(git_project), adapter=adapter)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
        ) as http:
            health = await http.get("/healthz")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            async with streamable_http_client("http://127.0.0.1:46200/mcp", http_client=http) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as client:
                    initialize = await asyncio.wait_for(client.initialize(), timeout=2.0)
                    assert initialize.serverInfo.name == "codemcp-remote-bridge"
                    tools = await client.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        "project_open",
                        "project_status",
                        "file_read",
                        "code_search",
                        "file_list",
                        "file_edit",
                        "file_create",
                        "file_write",
                        "file_move",
                        "file_delete",
                        "directory_create",
                        "registered_command_run",
                        "format_run",
                        "test_run",
                        "git_status",
                        "git_diff",
                        "checkpoint_create",
                        "checkpoint_restore",
                        "operation_status",
                        "approval_confirm",
                        "operation_cancel",
                        "operation_reconcile",
                    }

                    opened = _payload(
                        await client.call_tool("project_open", {"project_id": "demo"})
                    )
                    session_id = opened["data"]["session_id"]
                    assert opened["status"] == "succeeded"

                    read = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/hello.txt",
                            },
                        )
                    )
                    assert read["data"]["text"] == "fake read: hello.txt"
                    assert read["data"]["sha256"] == hashlib.sha256(b"hello\n").hexdigest()
                    assert read["data"]["size_bytes"] == len(b"hello\n")
                    assert adapter.calls[-1][0] == "ReadFile"

                    binary = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/binary.bin",
                            },
                        )
                    )
                    assert binary["error"]["code"] == "BINARY_FILE"

                    large = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/large.txt",
                            },
                        )
                    )
                    assert large["error"]["code"] == "FILE_TOO_LARGE"

                    escaped = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "../outside.txt",
                            },
                        )
                    )
                    assert escaped["error"]["code"] == "PATH_ESCAPE"

                    unknown = _payload(
                        await client.call_tool("project_open", {"project_id": "unknown"})
                    )
                    assert unknown["error"]["code"] == "PROJECT_NOT_ALLOWED"

                    registered_build = _payload(
                        await client.call_tool(
                            "registered_command_run",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "command_id": "build",
                                "client_request_id": "registered-command-build-1",
                                "request_hash": request_hash({"command_id": "build"}),
                            },
                        )
                    )
                    assert registered_build["status"] == "succeeded"
                    assert registered_build["data"]["command_id"] == "build"
                    assert registered_build["changed_files"] == []
                    assert "Code build successful" in registered_build["data"]["text"]
                    assert all(name != "RunCommand" for name, _ in adapter.calls)

                    unregistered_command = _payload(
                        await client.call_tool(
                            "registered_command_run",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "command_id": "missing",
                                "client_request_id": "registered-command-missing-1",
                                "request_hash": request_hash({"command_id": "missing"}),
                            },
                        )
                    )
                    assert unregistered_command["error"]["code"] == "COMMAND_NOT_ALLOWED"

                    approval = _payload(
                        await client.call_tool(
                            "format_run",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "command_id": "format",
                                "client_request_id": "format-1",
                                "request_hash": request_hash(
                                    {"command_id": "format", "expected_kind": "format"}
                                ),
                            },
                        )
                    )
                    assert approval["error"]["code"] == "APPROVAL_REQUIRED"

                    cancelled = _payload(
                        await client.call_tool(
                            "operation_cancel",
                            {
                                "operation_id": approval["operation_id"],
                                "session_id": session_id,
                                "client_request_id": "cancel-format-1",
                                "request_hash": request_hash(
                                    {"operation_id": approval["operation_id"]}
                                ),
                            },
                        )
                    )
                    assert cancelled["status"] == "cancelled"

                    (git_project / "src" / "hello.txt").write_text("dirty\n", encoding="utf-8")
                    dirty = _payload(
                        await client.call_tool(
                            "file_edit",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/hello.txt",
                                "old_string": "dirty",
                                "new_string": "changed",
                                "description": "test edit",
                                "client_request_id": "edit-1",
                                "request_hash": request_hash(
                                    _file_edit_input(
                                        "src/hello.txt", "dirty", "changed", "test edit"
                                    )
                                ),
                            },
                        )
                    )
                    assert dirty["error"]["code"] == "WORKSPACE_DIRTY"

    await service.close()
    assert not any(record.getMessage() == "Stateless session crashed" for record in caplog.records)


@pytest.mark.asyncio
async def test_code_search_excludes_sensitive_paths_before_and_after_grep(
    git_project: Path,
) -> None:
    (git_project / "local.env").write_text("TOKEN=do-not-return\n", encoding="utf-8")
    (git_project / "secrets").mkdir()
    (git_project / "secrets" / "private.key").write_text("private material\n", encoding="utf-8")
    adapter = SearchAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")

    result = await service.code_search(
        None,
        "demo",
        session.session_id,
        "private",
        None,
        None,
    )

    assert result["status"] == "succeeded"
    assert "hello.txt" in result["data"]["text"]
    assert "private.key" not in result["data"]["text"]
    assert "local.env" not in result["data"]["text"]
    assert all(path.name not in {"local.env", "secrets"} for path in adapter.search_paths)
    await service.close()


@pytest.mark.asyncio
async def test_file_list_is_bridge_local_without_codemcp_config_and_filters_sensitive_paths(
    git_project: Path,
) -> None:
    (git_project / "codemcp.toml").unlink()
    (git_project / "local.env").write_text("TOKEN=do-not-return\n", encoding="utf-8")
    (git_project / "secrets").mkdir()
    (git_project / "secrets" / "private.key").write_text("private material\n", encoding="utf-8")
    (git_project / ".hidden").write_text("hidden\n", encoding="utf-8")
    (git_project / "safe").mkdir()
    (git_project / "safe" / "nested").mkdir()
    (git_project / "safe" / "nested" / "visible.txt").write_text("visible\n", encoding="utf-8")

    adapter = FakeAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")

    root = await service.file_list(None, "demo", session.session_id, None)

    assert root["status"] == "succeeded"
    assert root["data"]["path"] == "."
    assert root["data"]["text"].startswith("- ./")
    assert "src/" in root["data"]["text"]
    assert "visible.txt" in root["data"]["text"]
    assert "local.env" not in root["data"]["text"]
    assert "secrets" not in root["data"]["text"]
    assert "private.key" not in root["data"]["text"]
    assert ".hidden" not in root["data"]["text"]
    assert all(name != "LS" for name, _ in adapter.calls)

    src = await service.file_list(None, "demo", session.session_id, "src")
    assert src["status"] == "succeeded"
    assert src["data"]["path"] == "src"
    assert src["data"]["text"].startswith("- src/")
    assert "hello.txt" in src["data"]["text"]
    assert all(name != "LS" for name, _ in adapter.calls)
    await service.close()


@pytest.mark.asyncio
async def test_file_edit_is_isolated_to_the_requested_target(git_project: Path) -> None:
    notes = git_project / "src" / "notes.txt"
    notes.write_text("baseline notes\n", encoding="utf-8")
    _git(git_project, "add", "src/notes.txt")
    _git(git_project, "commit", "-m", "test: add side effect target")

    adapter = MultiFileEditAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")
    operation_input = {
        "path": "src/hello.txt",
        "description": "report side effects",
        "old_string_digest": request_hash("hello"),
        "new_string_digest": request_hash("changed"),
    }

    result = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "changed",
        "report side effects",
        "edit-with-side-effect-1",
        request_hash(operation_input),
    )

    assert result["status"] == "succeeded"
    assert result["changed_files"] == ["src/hello.txt"]
    assert result["data"]["checkpoint"]["after"]["changed_files"] == ["src/hello.txt"]
    assert notes.read_text(encoding="utf-8") == "baseline notes\n"
    await service.close()


@pytest.mark.asyncio
async def test_file_edit_finalize_rejects_external_head_race(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")

    real_finalize = service.checkpoints.finalize

    async def race_finalize(*args: Any, **kwargs: Any) -> Any:
        (git_project / "src" / "external.txt").write_text("external amend\n", encoding="utf-8")
        _git(git_project, "add", "src/external.txt")
        _git(git_project, "commit", "--amend", "--no-edit")
        return await real_finalize(*args, **kwargs)

    monkeypatch.setattr(service.checkpoints, "finalize", race_finalize)
    description = "reject external finalize race"
    result = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "changed",
        description,
        "finalize-race-1",
        request_hash(_file_edit_input("src/hello.txt", "hello", "changed", description)),
    )

    assert result["status"] == "unknown"
    assert result["error"]["code"] == "UNKNOWN_SIDE_EFFECT"
    checkpoints = service.database.list_checkpoints(operation_id=result["operation_id"])
    assert len(checkpoints) == 1
    assert checkpoints[0].after_data is None
    assert _git(git_project, "status", "--porcelain") == ""
    await service.close()


@pytest.mark.asyncio
async def test_file_edit_finalize_rejects_head_race_after_fixed_diff(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")
    real_diff_names = service.checkpoints._git.diff_names_between
    raced = False

    async def race_after_snapshot(
        project_root: Path,
        *,
        ref_name: str,
        head: str,
    ) -> tuple[str, ...]:
        nonlocal raced
        if not raced:
            raced = True
            (git_project / "src" / "external-after-diff.txt").write_text(
                "external amend after snapshot\n",
                encoding="utf-8",
            )
            _git(git_project, "add", "src/external-after-diff.txt")
            _git(git_project, "commit", "--amend", "--no-edit")
        return await real_diff_names(project_root, ref_name=ref_name, head=head)

    monkeypatch.setattr(service.checkpoints._git, "diff_names_between", race_after_snapshot)
    description = "reject finalize terminal race"
    result = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "changed",
        description,
        "finalize-terminal-race-1",
        request_hash(_file_edit_input("src/hello.txt", "hello", "changed", description)),
    )

    assert result["status"] == "unknown"
    assert result["error"]["code"] == "UNKNOWN_SIDE_EFFECT"
    checkpoints = service.database.list_checkpoints(operation_id=result["operation_id"])
    assert len(checkpoints) == 1
    assert checkpoints[0].after_data is None
    assert _git(git_project, "status", "--porcelain") == ""
    await service.close()


@pytest.mark.asyncio
async def test_file_create_is_idempotent_and_never_overwrites(git_project: Path) -> None:
    adapter = WriteAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")

    existing_content = "must not overwrite\n"
    existing_description = "reject existing target"
    existing = await service.file_create(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        existing_content,
        existing_description,
        "create-existing-1",
        request_hash(
            _file_create_input(
                "src/hello.txt",
                existing_content,
                existing_description,
            )
        ),
    )
    assert existing["error"]["code"] == "CONFLICT"
    assert (git_project / "src" / "hello.txt").read_text(encoding="utf-8") == "hello\n"

    sensitive_content = "TOKEN=secret\n"
    sensitive_description = "reject sensitive target"
    sensitive = await service.file_create(
        None,
        "demo",
        session.session_id,
        "local.env",
        sensitive_content,
        sensitive_description,
        "create-sensitive-1",
        request_hash(
            _file_create_input(
                "local.env",
                sensitive_content,
                sensitive_description,
            )
        ),
    )
    assert sensitive["error"]["code"] == "SENSITIVE_PATH"

    content = "created through bridge\n"
    description = "create a new source file"
    operation_input = _file_create_input("src/created.txt", content, description)
    arguments = (
        None,
        "demo",
        session.session_id,
        "src/created.txt",
        content,
        description,
        "create-file-1",
        request_hash(operation_input),
    )
    first = await service.file_create(*arguments)
    second = await service.file_create(*arguments)

    assert first == second
    assert first["status"] == "succeeded"
    assert first["changed_files"] == ["src/created.txt"]
    assert first["data"]["checkpoint"]["after"]["changed_files"] == ["src/created.txt"]
    assert (git_project / "src" / "created.txt").read_text(encoding="utf-8") == content
    assert [name for name, _ in adapter.calls].count("WriteFile") == 0
    await service.close()


@pytest.mark.asyncio
async def test_file_write_requires_matching_sha256(git_project: Path) -> None:
    adapter = WriteAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")

    replacement = "whole file replacement\n"
    mismatch_description = "reject stale file baseline"
    mismatch_sha256 = "0" * 64
    mismatch = await service.file_write(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        replacement,
        mismatch_sha256,
        mismatch_description,
        "write-stale-1",
        request_hash(
            _file_write_input(
                "src/hello.txt",
                replacement,
                mismatch_sha256,
                mismatch_description,
            )
        ),
    )
    assert mismatch["error"]["code"] == "CONFLICT"
    assert mismatch["error"]["details"]["actual_sha256"] == hashlib.sha256(b"hello\n").hexdigest()
    assert [name for name, _ in adapter.calls].count("WriteFile") == 0

    invalid_description = "reject malformed digest"
    invalid_sha256 = "not-a-digest"
    invalid = await service.file_write(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        replacement,
        invalid_sha256,
        invalid_description,
        "write-invalid-digest-1",
        request_hash(
            _file_write_input(
                "src/hello.txt",
                replacement,
                invalid_sha256,
                invalid_description,
            )
        ),
    )
    assert invalid["error"]["code"] == "INVALID_REQUEST"
    assert [name for name, _ in adapter.calls].count("WriteFile") == 0

    expected_sha256 = hashlib.sha256(b"hello\n").hexdigest()
    description = "replace the complete file"
    success = await service.file_write(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        replacement,
        expected_sha256,
        description,
        "write-file-1",
        request_hash(
            _file_write_input(
                "src/hello.txt",
                replacement,
                expected_sha256,
                description,
            )
        ),
    )

    assert success["status"] == "succeeded"
    assert success["changed_files"] == ["src/hello.txt"]
    assert success["data"]["checkpoint"]["after"]["changed_files"] == ["src/hello.txt"]
    assert (git_project / "src" / "hello.txt").read_text(encoding="utf-8") == replacement
    assert [name for name, _ in adapter.calls].count("WriteFile") == 0
    await service.close()


@pytest.mark.asyncio
async def test_file_move_is_idempotent_no_clobber_and_safe(git_project: Path) -> None:
    service = create_app(_settings(git_project), adapter=WriteAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")

    same_description = "reject a no-op move"
    same = await service.file_move(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "src/hello.txt",
        same_description,
        "move-same-1",
        request_hash(
            _file_move_input(
                "src/hello.txt",
                "src/hello.txt",
                same_description,
            )
        ),
    )
    assert same["error"]["code"] == "INVALID_REQUEST"

    conflict_description = "never overwrite an existing destination"
    conflict = await service.file_move(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "src/large.txt",
        conflict_description,
        "move-conflict-1",
        request_hash(
            _file_move_input(
                "src/hello.txt",
                "src/large.txt",
                conflict_description,
            )
        ),
    )
    assert conflict["error"]["code"] == "CONFLICT"
    assert (git_project / "src" / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    assert (git_project / "src" / "large.txt").read_text(encoding="utf-8") == "x" * 1025

    sensitive_description = "reject a sensitive destination"
    sensitive = await service.file_move(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "local.env",
        sensitive_description,
        "move-sensitive-1",
        request_hash(
            _file_move_input(
                "src/hello.txt",
                "local.env",
                sensitive_description,
            )
        ),
    )
    assert sensitive["error"]["code"] == "SENSITIVE_PATH"

    missing_parent_description = "reject a missing destination parent"
    missing_parent = await service.file_move(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "missing/moved.txt",
        missing_parent_description,
        "move-missing-parent-1",
        request_hash(
            _file_move_input(
                "src/hello.txt",
                "missing/moved.txt",
                missing_parent_description,
            )
        ),
    )
    assert missing_parent["error"]["code"] == "FILE_NOT_FOUND"

    (git_project / ".gitignore").write_text("src/ignored.tmp\n", encoding="utf-8")
    _git(git_project, "add", ".gitignore")
    _git(git_project, "commit", "-m", "test: ignore untracked move source")
    ignored = git_project / "src" / "ignored.tmp"
    ignored.write_text("ignored\n", encoding="utf-8")
    assert _git(git_project, "status", "--porcelain") == ""

    untracked_description = "reject an ignored untracked source"
    untracked = await service.file_move(
        None,
        "demo",
        session.session_id,
        "src/ignored.tmp",
        "src/ignored-moved.tmp",
        untracked_description,
        "move-untracked-1",
        request_hash(
            _file_move_input(
                "src/ignored.tmp",
                "src/ignored-moved.tmp",
                untracked_description,
            )
        ),
    )
    assert untracked["error"]["code"] == "CONFLICT"
    assert ignored.is_file()

    description = "move a tracked source file without overwriting"
    operation_input = _file_move_input("src/hello.txt", "src/moved.txt", description)
    arguments = (
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "src/moved.txt",
        description,
        "move-file-1",
        request_hash(operation_input),
    )
    before_head = _git(git_project, "rev-parse", "HEAD")
    first = await service.file_move(*arguments)
    second = await service.file_move(*arguments)

    assert first == second
    assert first["status"] == "succeeded"
    assert set(first["changed_files"]) == {"src/hello.txt", "src/moved.txt"}
    assert set(first["data"]["checkpoint"]["after"]["changed_files"]) == {
        "src/hello.txt",
        "src/moved.txt",
    }
    assert first["data"]["source_path"] == "src/hello.txt"
    assert first["data"]["destination_path"] == "src/moved.txt"
    assert first["data"]["head"] != before_head
    assert first["data"]["checkpoint"]["after"]["head"] == first["data"]["head"]
    assert _git(git_project, "rev-list", "--count", "main") == "3"
    assert _git(git_project, "rev-parse", f"{first['data']['head']}^") == before_head
    assert not (git_project / "src" / "hello.txt").exists()
    assert (git_project / "src" / "moved.txt").read_text(encoding="utf-8") == "hello\n"
    assert _git(git_project, "status", "--porcelain") == ""

    await service.close()


@pytest.mark.asyncio
async def test_file_delete_is_idempotent_tracked_only_and_safe(git_project: Path) -> None:
    service = create_app(_settings(git_project), adapter=WriteAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")

    missing_description = "reject a missing delete target"
    missing = await service.file_delete(
        None,
        "demo",
        session.session_id,
        "src/missing.txt",
        missing_description,
        "delete-missing-1",
        request_hash(_file_delete_input("src/missing.txt", missing_description)),
    )
    assert missing["error"]["code"] == "FILE_NOT_FOUND"

    sensitive_description = "reject a sensitive delete target"
    sensitive = await service.file_delete(
        None,
        "demo",
        session.session_id,
        "local.env",
        sensitive_description,
        "delete-sensitive-1",
        request_hash(_file_delete_input("local.env", sensitive_description)),
    )
    assert sensitive["error"]["code"] == "SENSITIVE_PATH"

    (git_project / ".gitignore").write_text("src/ignored.tmp\n", encoding="utf-8")
    _git(git_project, "add", ".gitignore")
    _git(git_project, "commit", "-m", "test: ignore untracked delete source")
    ignored = git_project / "src" / "ignored.tmp"
    ignored.write_text("ignored\n", encoding="utf-8")
    assert _git(git_project, "status", "--porcelain") == ""

    untracked_description = "reject an ignored untracked delete source"
    untracked = await service.file_delete(
        None,
        "demo",
        session.session_id,
        "src/ignored.tmp",
        untracked_description,
        "delete-untracked-1",
        request_hash(_file_delete_input("src/ignored.tmp", untracked_description)),
    )
    assert untracked["error"]["code"] == "CONFLICT"
    assert ignored.is_file()

    description = "delete one tracked file through the Bridge"
    operation_input = _file_delete_input("src/hello.txt", description)
    arguments = (
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        description,
        "delete-file-1",
        request_hash(operation_input),
    )
    before_head = _git(git_project, "rev-parse", "HEAD")
    first = await service.file_delete(*arguments)
    second = await service.file_delete(*arguments)

    assert first == second
    assert first["status"] == "succeeded"
    assert first["changed_files"] == ["src/hello.txt"]
    assert first["data"]["checkpoint"]["after"]["changed_files"] == ["src/hello.txt"]
    assert first["data"]["path"] == "src/hello.txt"
    assert first["data"]["head"] != before_head
    assert first["data"]["checkpoint"]["after"]["head"] == first["data"]["head"]
    assert _git(git_project, "rev-list", "--count", "main") == "3"
    assert _git(git_project, "rev-parse", f"{first['data']['head']}^") == before_head
    assert not (git_project / "src" / "hello.txt").exists()
    assert _git(git_project, "status", "--porcelain") == ""

    await service.close()


@pytest.mark.asyncio
async def test_session_wip_create_then_delete_can_amend_to_empty_commit(
    git_project: Path,
) -> None:
    service = create_app(_settings(git_project), adapter=WriteAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")
    baseline_head = _git(git_project, "rev-parse", "HEAD")
    baseline_count = int(_git(git_project, "rev-list", "--count", "main"))

    content = "temporary session file\n"
    create_description = "create temporary session file"
    created = await service.file_create(
        None,
        "demo",
        session.session_id,
        "src/session-temporary.txt",
        content,
        create_description,
        "session-temporary-create-1",
        request_hash(
            _file_create_input(
                "src/session-temporary.txt",
                content,
                create_description,
            )
        ),
    )
    assert created["status"] == "succeeded"
    created_head = created["data"]["checkpoint"]["after"]["head"]
    assert int(_git(git_project, "rev-list", "--count", "main")) == baseline_count + 1

    delete_description = "delete temporary session file"
    deleted = await service.file_delete(
        None,
        "demo",
        session.session_id,
        "src/session-temporary.txt",
        delete_description,
        "session-temporary-delete-1",
        request_hash(
            _file_delete_input(
                "src/session-temporary.txt",
                delete_description,
            )
        ),
    )

    assert deleted["status"] == "succeeded"
    deleted_head = deleted["data"]["checkpoint"]["after"]["head"]
    assert deleted_head != created_head
    assert deleted["changed_files"] == ["src/session-temporary.txt"]
    assert deleted["data"]["checkpoint"]["after"]["changed_files"] == ["src/session-temporary.txt"]
    assert not (git_project / "src" / "session-temporary.txt").exists()
    assert _git(git_project, "status", "--porcelain") == ""
    assert int(_git(git_project, "rev-list", "--count", "main")) == baseline_count + 1
    assert _git(git_project, "rev-parse", f"{deleted_head}^") == baseline_head
    assert _git(git_project, "diff", "--name-only", baseline_head, deleted_head) == ""
    assert (
        await service.git.read_session_footer(
            git_project,
            head=deleted_head,
        )
        == session.session_id
    )

    await service.close()


@pytest.mark.asyncio
async def test_directory_create_is_git_trackable_idempotent_and_safe(
    git_project: Path,
) -> None:
    adapter = WriteAdapter()
    service = create_app(_settings(git_project), adapter=adapter)[1]
    await service.start()
    session = service.sessions.create("demo")

    existing_description = "reject existing directory"
    existing = await service.directory_create(
        None,
        "demo",
        session.session_id,
        "src",
        existing_description,
        "directory-existing-1",
        request_hash(_directory_create_input("src", existing_description)),
    )
    assert existing["error"]["code"] == "CONFLICT"

    missing_parent_description = "reject missing parent"
    missing_parent = await service.directory_create(
        None,
        "demo",
        session.session_id,
        "missing/child",
        missing_parent_description,
        "directory-missing-parent-1",
        request_hash(
            _directory_create_input("missing/child", missing_parent_description),
        ),
    )
    assert missing_parent["error"]["code"] == "FILE_NOT_FOUND"

    sensitive_description = "reject sensitive directory"
    sensitive = await service.directory_create(
        None,
        "demo",
        session.session_id,
        "secrets",
        sensitive_description,
        "directory-sensitive-1",
        request_hash(_directory_create_input("secrets", sensitive_description)),
    )
    assert sensitive["error"]["code"] == "SENSITIVE_PATH"

    description = "create a git trackable source directory"
    operation_input = _directory_create_input("src/generated", description)
    arguments = (
        None,
        "demo",
        session.session_id,
        "src/generated",
        description,
        "directory-create-1",
        request_hash(operation_input),
    )
    before_head = _git(git_project, "rev-parse", "HEAD")
    first = await service.directory_create(*arguments)
    second = await service.directory_create(*arguments)

    marker = git_project / "src" / "generated" / ".gitkeep"
    assert first == second
    assert first["status"] == "succeeded"
    assert first["data"]["path"] == "src/generated"
    assert first["data"]["marker_path"] == "src/generated/.gitkeep"
    assert first["changed_files"] == ["src/generated/.gitkeep"]
    assert first["data"]["checkpoint"]["after"]["changed_files"] == ["src/generated/.gitkeep"]
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert (
        _git(git_project, "rev-parse", f"{first['data']['checkpoint']['after']['head']}^")
        == before_head
    )
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == ""
    assert [name for name, _ in adapter.calls].count("WriteFile") == 0
    await service.close()


@pytest.mark.asyncio
async def test_session_wip_merges_all_file_mutations_without_cross_session_amend(
    git_project: Path,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]
    await service.start()
    session_a = service.sessions.create("demo")
    baseline_head = _git(git_project, "rev-parse", "HEAD")
    baseline_count = int(_git(git_project, "rev-list", "--count", "main"))

    async def assert_checkpoint(result: dict[str, Any], previous_head: str) -> str:
        assert result["status"] == "succeeded"
        checkpoint = result["data"]["checkpoint"]
        new_head = checkpoint["after"]["head"]
        assert checkpoint["before"]["head"] == previous_head
        assert new_head != previous_head
        assert len(checkpoint["diff_hash"]) == 64
        assert _git(git_project, "status", "--porcelain") == ""
        assert int(_git(git_project, "rev-list", "--count", "main")) == baseline_count + 1
        return new_head

    edit_description = "session A edit"
    edited = await service.file_edit(
        None,
        "demo",
        session_a.session_id,
        "src/hello.txt",
        "hello",
        "session A",
        edit_description,
        "session-a-edit-1",
        request_hash(_file_edit_input("src/hello.txt", "hello", "session A", edit_description)),
    )
    head_1 = await assert_checkpoint(edited, baseline_head)
    assert _git(git_project, "rev-parse", f"{head_1}^") == baseline_head
    assert await service.git.read_session_footer(git_project, head=head_1) == session_a.session_id

    created_content = "session A created\n"
    create_description = "session A create"
    created = await service.file_create(
        None,
        "demo",
        session_a.session_id,
        "src/session-a.txt",
        created_content,
        create_description,
        "session-a-create-1",
        request_hash(_file_create_input("src/session-a.txt", created_content, create_description)),
    )
    head_2 = await assert_checkpoint(created, head_1)
    assert created["data"]["checkpoint"]["after"]["changed_files"] == ["src/session-a.txt"]

    written_content = "session A written\n"
    write_description = "session A write"
    expected_sha256 = hashlib.sha256(created_content.encode("utf-8")).hexdigest()
    written = await service.file_write(
        None,
        "demo",
        session_a.session_id,
        "src/session-a.txt",
        written_content,
        expected_sha256,
        write_description,
        "session-a-write-1",
        request_hash(
            _file_write_input(
                "src/session-a.txt",
                written_content,
                expected_sha256,
                write_description,
            )
        ),
    )
    head_3 = await assert_checkpoint(written, head_2)
    assert written["data"]["checkpoint"]["after"]["changed_files"] == ["src/session-a.txt"]

    move_description = "session A move"
    moved = await service.file_move(
        None,
        "demo",
        session_a.session_id,
        "src/session-a.txt",
        "src/session-a-moved.txt",
        move_description,
        "session-a-move-1",
        request_hash(
            _file_move_input("src/session-a.txt", "src/session-a-moved.txt", move_description)
        ),
    )
    head_4 = await assert_checkpoint(moved, head_3)
    assert set(moved["data"]["checkpoint"]["after"]["changed_files"]) == {
        "src/session-a.txt",
        "src/session-a-moved.txt",
    }

    delete_description = "session A delete"
    deleted = await service.file_delete(
        None,
        "demo",
        session_a.session_id,
        "src/session-a-moved.txt",
        delete_description,
        "session-a-delete-1",
        request_hash(_file_delete_input("src/session-a-moved.txt", delete_description)),
    )
    head_5 = await assert_checkpoint(deleted, head_4)
    assert deleted["data"]["checkpoint"]["after"]["changed_files"] == ["src/session-a-moved.txt"]

    directory_description = "session A directory"
    directory = await service.directory_create(
        None,
        "demo",
        session_a.session_id,
        "src/session-a-directory",
        directory_description,
        "session-a-directory-1",
        request_hash(_directory_create_input("src/session-a-directory", directory_description)),
    )
    head_6 = await assert_checkpoint(directory, head_5)
    assert directory["data"]["checkpoint"]["after"]["changed_files"] == [
        "src/session-a-directory/.gitkeep"
    ]
    assert await service.git.read_session_footer(git_project, head=head_6) == session_a.session_id

    session_b = service.sessions.create("demo")
    b_description = "session B edit"
    b_edit = await service.file_edit(
        None,
        "demo",
        session_b.session_id,
        "src/hello.txt",
        "session A",
        "session B",
        b_description,
        "session-b-edit-1",
        request_hash(_file_edit_input("src/hello.txt", "session A", "session B", b_description)),
    )
    assert b_edit["status"] == "succeeded"
    b_checkpoint = b_edit["data"]["checkpoint"]
    b_head = b_checkpoint["after"]["head"]
    assert b_checkpoint["before"]["head"] == head_6
    assert b_head != head_6
    assert _git(git_project, "status", "--porcelain") == ""
    assert int(_git(git_project, "rev-list", "--count", "main")) == baseline_count + 2
    assert _git(git_project, "rev-parse", f"{b_head}^") == head_6
    assert await service.git.read_session_footer(git_project, head=b_head) == session_b.session_id
    await service.close()


@pytest.mark.asyncio
async def test_noop_file_edit_does_not_establish_session_wip_ownership(
    git_project: Path,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]
    await service.start()
    session = service.sessions.create("demo")
    baseline_head = _git(git_project, "rev-parse", "HEAD")
    baseline_count = _git(git_project, "rev-list", "--count", "main")

    noop_description = "same content no-op"
    noop = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "hello",
        noop_description,
        "noop-edit-1",
        request_hash(_file_edit_input("src/hello.txt", "hello", "hello", noop_description)),
    )
    assert noop["status"] == "succeeded"
    assert noop["changed_files"] == []
    assert noop["data"]["checkpoint"]["after"]["head"] == baseline_head
    assert _git(git_project, "rev-list", "--count", "main") == baseline_count

    description = "first real edit after no-op"
    real = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "real change",
        description,
        "real-edit-1",
        request_hash(_file_edit_input("src/hello.txt", "hello", "real change", description)),
    )
    assert real["status"] == "succeeded"
    real_head = real["data"]["checkpoint"]["after"]["head"]
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert _git(git_project, "rev-parse", f"{real_head}^") == baseline_head
    assert await service.git.read_session_footer(git_project, head=real_head) == session.session_id
    await service.close()


@pytest.mark.asyncio
async def test_static_zero_request_id_does_not_conflict_for_read_operations(
    git_project: Path,
) -> None:
    adapter = FakeAdapter()
    app, service = create_app(_settings(git_project), adapter=adapter)
    context = SimpleNamespace(request_id="0")

    async with app.router.lifespan_context(app):
        opened = await service.project_open(context, "demo")
        session_id = opened["data"]["session_id"]
        responses = [
            await service.project_status(context, "demo", session_id),
            await service.file_read(context, "demo", session_id, "src/hello.txt", None, None),
            await service.code_search(context, "demo", session_id, "hello", None, None),
            await service.file_list(context, "demo", session_id, "src"),
        ]

    assert all(response["status"] == "succeeded" for response in responses)
    assert all(response["request_id"] == "0" for response in responses)
    assert len({response["operation_id"] for response in responses}) == len(responses)

    await service.close()


@pytest.mark.asyncio
async def test_phase3_idempotency_approval_and_operation_status(git_project: Path) -> None:
    adapter = FakeAdapter()
    app, service = create_app(_settings(git_project), adapter=adapter)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
        ) as http:
            async with streamable_http_client("http://127.0.0.1:46200/mcp", http_client=http) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as client:
                    await client.initialize()
                    opened = _payload(
                        await client.call_tool("project_open", {"project_id": "demo"})
                    )
                    session_id = opened["data"]["session_id"]

                    edit_arguments = {
                        "project_id": "demo",
                        "session_id": session_id,
                        "path": "src/hello.txt",
                        "old_string": "hello",
                        "new_string": "changed",
                        "description": "idempotent edit",
                        "client_request_id": "edit-replay-1",
                        "request_hash": request_hash(
                            _file_edit_input("src/hello.txt", "hello", "changed", "idempotent edit")
                        ),
                    }
                    first_edit = _payload(await client.call_tool("file_edit", edit_arguments))
                    second_edit = _payload(await client.call_tool("file_edit", edit_arguments))
                    assert first_edit == second_edit
                    assert [name for name, _ in adapter.calls].count("EditFile") == 0

                    approval = _payload(
                        await client.call_tool(
                            "registered_command_run",
                            {
                                "project_id": "demo",
                                "session_id": session_id,
                                "command_id": "format",
                                "client_request_id": "registered-command-approval-1",
                                "request_hash": request_hash({"command_id": "format"}),
                            },
                        )
                    )
                    assert approval["status"] == "awaiting_approval"
                    operation_id = approval["operation_id"]
                    token = approval["error"]["details"]["approval_token"]

                    pending_status = _payload(
                        await client.call_tool(
                            "operation_status",
                            {"operation_id": operation_id, "session_id": session_id},
                        )
                    )
                    assert pending_status["data"]["state"] == "awaiting_approval"
                    assert "approval_token" not in str(pending_status["data"])

                    confirmed = _payload(
                        await client.call_tool(
                            "approval_confirm",
                            {
                                "operation_id": operation_id,
                                "session_id": session_id,
                                "approval_token": token,
                                "client_request_id": "approval-confirm-1",
                                "request_hash": request_hash(
                                    {
                                        "operation_id": operation_id,
                                        "approval_token_digest": request_hash(token),
                                    }
                                ),
                            },
                        )
                    )
                    assert confirmed["status"] == "succeeded"
                    assert confirmed["data"]["approved_operation"]["status"] == "succeeded"
                    assert (
                        "Code format successful"
                        in confirmed["data"]["approved_operation"]["data"]["text"]
                    )
                    assert all(name != "RunCommand" for name, _ in adapter.calls)

                    final_status = _payload(
                        await client.call_tool(
                            "operation_status",
                            {"operation_id": operation_id, "session_id": session_id},
                        )
                    )
                    assert final_status["data"]["state"] == "succeeded"
                    event_types = {
                        event["event_type"] for event in final_status["data"]["audit_events"]
                    }
                    assert "approval.created" in event_types
                    assert "approval.consumed" in event_types

    await service.close()


@pytest.mark.asyncio
async def test_phase3_reconcile_unknown_mutation_releases_project_lock(
    git_project: Path,
) -> None:
    adapter = FakeAdapter()
    app, service = create_app(_settings(git_project), adapter=adapter)
    await service.start()
    session = service.sessions.create("demo")
    input_data = {"path": "src/hello.txt", "new_string_digest": "digest"}
    started = service.operations.start(
        operation_id="unknown-operation",
        project_id="demo",
        session_id=session.session_id,
        kind="file_edit",
        mutation=True,
        client_request_id="unknown-edit-1",
        supplied_request_hash=request_hash(input_data),
        input_data=input_data,
    )
    service.operations.dispatch(started.record.operation_id)
    unknown = error_payload(
        request_id="unknown-request",
        session_id=session.session_id,
        project_id="demo",
        operation_id=started.record.operation_id,
        error=BridgeError(
            "UNKNOWN_SIDE_EFFECT",
            "mutation outcome is unknown and requires reconciliation",
            status="unknown",
        ),
    )
    service.operations.finish(started.record.operation_id, state="unknown", payload=unknown)

    other_session = service.sessions.create("demo")
    foreign_status = await service.operation_status(
        None, started.record.operation_id, other_session.session_id
    )
    assert foreign_status["error"]["code"] == "OPERATION_NOT_FOUND"
    foreign_reconcile = await service.operation_reconcile(
        None,
        started.record.operation_id,
        other_session.session_id,
        "failed",
        "foreign session must not reconcile",
        "foreign-reconcile-1",
        request_hash(
            {
                "operation_id": started.record.operation_id,
                "decision": "failed",
                "evidence_digest": request_hash("foreign session must not reconcile"),
            }
        ),
    )
    assert foreign_reconcile["error"]["code"] == "OPERATION_NOT_FOUND"
    assert service.operations.operation(started.record.operation_id).state == "unknown"

    evidence = "backend confirmed that no mutation was applied"
    reconciled = await service.operation_reconcile(
        None,
        started.record.operation_id,
        session.session_id,
        "failed",
        evidence,
        "reconcile-unknown-1",
        request_hash(
            {
                "operation_id": started.record.operation_id,
                "decision": "failed",
                "evidence_digest": request_hash(evidence),
            }
        ),
    )
    assert reconciled["status"] == "failed"
    assert reconciled["data"]["reconciled_operation"]["error"]["details"]["reconciled"]
    assert service.operations.operation(started.record.operation_id).state == "failed"

    edit_input = {
        "path": "src/hello.txt",
        "description": "post-reconcile edit",
        "old_string_digest": request_hash("hello"),
        "new_string_digest": request_hash("changed"),
    }
    edit = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "changed",
        "post-reconcile edit",
        "post-reconcile-edit-1",
        request_hash(edit_input),
    )
    assert edit["status"] == "succeeded"
    await service.close()


@pytest.mark.asyncio
async def test_cancelled_mutation_is_persisted_as_unknown(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]

    async def cancelled_file_commit(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(service.git, "commit_file_bytes", cancelled_file_commit)
    await service.start()
    session = service.sessions.create("demo")

    description = "cancelled edit"
    operation_input = _file_edit_input("src/hello.txt", "hello", "changed", description)
    with pytest.raises(asyncio.CancelledError):
        await service.file_edit(
            None,
            "demo",
            session.session_id,
            "src/hello.txt",
            "hello",
            "changed",
            description,
            "cancelled-edit-1",
            request_hash(operation_input),
        )

    blocked_description = "edit after cancellation"
    blocked_input = _file_edit_input("src/hello.txt", "hello", "changed again", blocked_description)
    blocked = await service.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "changed again",
        blocked_description,
        "edit-after-cancel-1",
        request_hash(blocked_input),
    )
    assert blocked["error"]["code"] == "OPERATION_BLOCKED"
    operation_id = blocked["error"]["details"]["operation_id"]
    persisted = service.operations.operation(operation_id)
    assert persisted.state == "unknown"
    assert persisted.error_data["code"] == "UNKNOWN_SIDE_EFFECT"
    await service.close()


@pytest.mark.asyncio
async def test_graceful_shutdown_successor_can_inspect_and_reconcile_unknown_mutation(
    git_project: Path,
) -> None:
    settings = _settings(git_project)
    service = create_app(settings, adapter=FakeAdapter())[1]
    await service.start()
    origin = service.sessions.create("demo")
    input_data = {"path": "src/hello.txt", "new_string_digest": "digest"}
    started = service.operations.start(
        operation_id="graceful-shutdown-unknown-operation",
        project_id="demo",
        session_id=origin.session_id,
        kind="file_edit",
        mutation=True,
        client_request_id="graceful-shutdown-unknown-edit-1",
        supplied_request_hash=request_hash(input_data),
        input_data=input_data,
    )
    service.operations.dispatch(started.record.operation_id)
    unknown = error_payload(
        request_id="graceful-shutdown-unknown-request",
        session_id=origin.session_id,
        project_id="demo",
        operation_id=started.record.operation_id,
        error=BridgeError(
            "UNKNOWN_SIDE_EFFECT",
            "mutation outcome is unknown before graceful shutdown",
            status="unknown",
        ),
    )
    service.operations.finish(started.record.operation_id, state="unknown", payload=unknown)
    await service.close()

    restarted = create_app(settings, adapter=FakeAdapter())[1]
    await restarted.start()
    successor = restarted.sessions.create("demo")

    observed = await restarted.operation_status(
        None,
        started.record.operation_id,
        successor.session_id,
    )
    assert observed["status"] == "succeeded"
    assert observed["data"]["state"] == "unknown"

    evidence = (
        "graceful restart preserved the unknown operation and no mutation is accepted as successful"
    )
    reconciled = await restarted.operation_reconcile(
        None,
        started.record.operation_id,
        successor.session_id,
        "failed",
        evidence,
        "graceful-shutdown-reconcile-1",
        request_hash(
            {
                "operation_id": started.record.operation_id,
                "decision": "failed",
                "evidence_digest": request_hash(evidence),
            }
        ),
    )
    assert reconciled["status"] == "failed"
    assert restarted.operations.operation(started.record.operation_id).state == "failed"

    edit_description = "mutation after graceful shutdown reconcile"
    edited = await restarted.file_edit(
        None,
        "demo",
        successor.session_id,
        "src/hello.txt",
        "hello",
        "changed",
        edit_description,
        "post-graceful-reconcile-edit-1",
        request_hash(
            _file_edit_input(
                "src/hello.txt",
                "hello",
                "changed",
                edit_description,
            )
        ),
    )
    assert edited["status"] == "succeeded"
    await restarted.close()


@pytest.mark.asyncio
async def test_restart_successor_session_can_reconcile_applied_unknown_mutation(
    git_project: Path,
) -> None:
    service = create_app(_settings(git_project), adapter=FakeAdapter())[1]
    await service.start()
    origin = service.sessions.create("demo")
    input_data = {"path": "src/hello.txt", "new_string_digest": "digest"}
    started = service.operations.start(
        operation_id="restart-unknown-operation",
        project_id="demo",
        session_id=origin.session_id,
        kind="file_edit",
        mutation=True,
        client_request_id="restart-unknown-edit-1",
        supplied_request_hash=request_hash(input_data),
        input_data=input_data,
    )
    service.operations.dispatch(started.record.operation_id)
    project = service.registry.get("demo")
    checkpoint = await service.checkpoints.create(
        project,
        session_id=origin.session_id,
        operation_id=started.record.operation_id,
        kind="mutation",
    )

    target = git_project / "src" / "hello.txt"
    target.write_text("changed\n", encoding="utf-8")
    _git(git_project, "add", "src/hello.txt")
    _git(git_project, "commit", "--amend", "--no-edit")
    unknown = error_payload(
        request_id="restart-unknown-request",
        session_id=origin.session_id,
        project_id="demo",
        operation_id=started.record.operation_id,
        error=BridgeError(
            "UNKNOWN_SIDE_EFFECT",
            "mutation completed but response was lost",
            status="unknown",
        ),
    )
    service.operations.finish(started.record.operation_id, state="unknown", payload=unknown)
    service.database.transition_session(
        origin.session_id,
        "blocked",
        reason="bridge_restart",
    )
    successor = service.sessions.create("demo")
    evidence = "operator verified the committed Git mutation after the response was lost"
    reconciled = await service.operation_reconcile(
        None,
        started.record.operation_id,
        successor.session_id,
        "succeeded",
        evidence,
        "restart-success-reconcile-1",
        request_hash(
            {
                "operation_id": started.record.operation_id,
                "decision": "succeeded",
                "evidence_digest": request_hash(evidence),
            }
        ),
    )

    assert reconciled["status"] == "succeeded"
    original = service.operations.operation(started.record.operation_id)
    assert original.state == "succeeded"
    assert original.result_data["changed_files"] == ["src/hello.txt"]
    finalized = service.database.get_checkpoint(checkpoint.checkpoint_id)
    assert finalized is not None
    assert finalized.after_data is not None
    assert finalized.after_data["changed_files"] == ["src/hello.txt"]

    successor_head_before = _git(git_project, "rev-parse", "HEAD")
    successor_count_before = int(_git(git_project, "rev-list", "--count", "main"))
    successor_description = "successor starts a new WIP"
    successor_edit = await service.file_edit(
        None,
        "demo",
        successor.session_id,
        "src/hello.txt",
        "changed",
        "successor change",
        successor_description,
        "successor-edit-after-reconcile-1",
        request_hash(
            _file_edit_input(
                "src/hello.txt",
                "changed",
                "successor change",
                successor_description,
            )
        ),
    )
    assert successor_edit["status"] == "succeeded"
    successor_head = successor_edit["data"]["checkpoint"]["after"]["head"]
    assert successor_head != successor_head_before
    assert int(_git(git_project, "rev-list", "--count", "main")) == successor_count_before + 1
    assert _git(git_project, "rev-parse", f"{successor_head}^") == successor_head_before
    assert (
        await service.git.read_session_footer(
            git_project,
            head=successor_head,
        )
        == successor.session_id
    )
    await service.close()


@pytest.mark.asyncio
async def test_stateless_http_waits_for_responder_exit_before_transport_eof(
    git_project: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, service = create_server(_settings(git_project), adapter=FakeAdapter())
    app = server.streamable_http_app()
    low_level = server._mcp_server  # noqa: SLF001
    original_handle_request = low_level._handle_request  # noqa: SLF001
    response_sent = asyncio.Event()
    release_handler = asyncio.Event()

    async def delayed_handle_request(
        message: Any,
        req: Any,
        session: Any,
        lifespan_context: Any,
        raise_exceptions: bool,
    ) -> None:
        await original_handle_request(
            message,
            req,
            session,
            lifespan_context,
            raise_exceptions,
        )
        response_sent.set()
        await release_handler.wait()

    low_level._handle_request = delayed_handle_request  # type: ignore[method-assign]  # noqa: SLF001
    caplog.set_level(logging.ERROR, logger="codemcp_bridge.mcp_transport")

    async with app.router.lifespan_context(app):
        opened = await service.project_open(None, "demo")
        session_id = opened["data"]["session_id"]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
        ) as http:
            request = asyncio.create_task(
                http.post(
                    "/mcp",
                    headers={
                        "accept": "application/json",
                        "content-type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 43,
                        "method": "tools/call",
                        "params": {
                            "name": "file_read",
                            "arguments": {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/hello.txt",
                            },
                        },
                    },
                )
            )
            await asyncio.wait_for(response_sent.wait(), timeout=1)
            # The previous transport closed Server.run after 250ms even though
            # the responder was still executing, which cancelled slow mutations.
            await asyncio.sleep(0.35)
            assert not request.done()

            release_handler.set()
            response = await asyncio.wait_for(request, timeout=1)
            assert response.status_code == 200

    assert not any(
        record.name == "codemcp_bridge.mcp_transport"
        and "Stateless session crashed" in record.getMessage()
        for record in caplog.records
    )
    await service.close()


@pytest.mark.asyncio
async def test_stateless_http_client_cancellation_does_not_crash_responder(
    git_project: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = SlowReadAdapter()
    app, service = create_app(_settings(git_project), adapter=adapter)
    caplog.set_level(logging.ERROR, logger="codemcp_bridge.mcp_transport")

    async with app.router.lifespan_context(app):
        opened = await service.project_open(None, "demo")
        session_id = opened["data"]["session_id"]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
        ) as http:
            request = asyncio.create_task(
                http.post(
                    "/mcp",
                    headers={"accept": "application/json"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 42,
                        "method": "tools/call",
                        "params": {
                            "name": "file_read",
                            "arguments": {
                                "project_id": "demo",
                                "session_id": session_id,
                                "path": "src/hello.txt",
                            },
                        },
                    },
                )
            )
            await asyncio.wait_for(adapter.started.wait(), timeout=1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            await asyncio.sleep(0.1)

    assert not any(
        record.name == "codemcp_bridge.mcp_transport"
        and "Stateless session crashed" in record.getMessage()
        for record in caplog.records
    )
    await service.close()
