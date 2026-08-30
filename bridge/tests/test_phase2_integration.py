from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codemcp_bridge.mcp_server import create_app
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


def _git(project: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=project, check=True, capture_output=True)


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


def _settings(project: Path, data_dir: Path) -> BridgeSettings:
    format_command = CommandSpec(
        command_id="format",
        kind="format",
        argv=("git", "--version"),
        timeout_seconds=30,
        approval="not-required",
    )
    test_script = "print('test passed')"
    test_command = CommandSpec(
        command_id="test",
        kind="test",
        argv=("python", "-c", test_script),
        timeout_seconds=30,
        approval="not-required",
    )
    spec = ProjectSpec(
        project_id="integration",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={"format": format_command, "test": test_command},
    )
    repository_root = Path(__file__).resolve().parents[2]
    return BridgeSettings(
        repository_root=repository_root,
        bridge_config_path=data_dir / "bridge.toml",
        projects_config_path=data_dir / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(data_dir, data_dir / "bridge.sqlite3", data_dir / "logs"),
        policy=PolicySettings(False, False, False, True, 4096, 16_384, "per-project"),
        codemcp=CodemcpSettings("wsl2", "Ubuntu", None, 30, 60, 5),
        projects={"integration": spec},
    )


@pytest.fixture
def git_project() -> Iterator[Path]:
    repository_root = Path(__file__).resolve().parents[2]
    run_root = repository_root / ".local" / f"phase2-integration-{uuid.uuid4().hex}"
    run_root.mkdir(parents=True)
    project = run_root / "phase2 中文 project with spaces"
    (project / "src").mkdir(parents=True)
    (project / "src" / "hello file.txt").write_text(
        "hello codemcp\nsecond line\n", encoding="utf-8"
    )
    (project / "src" / "notes.txt").write_text("baseline notes\n", encoding="utf-8")
    (project / "src" / "binary.bin").write_bytes(b"header\x00binary\n")
    (project / "src" / "large.txt").write_text("x" * 4097, encoding="utf-8")
    test_script = "print('test passed')"
    (project / "codemcp.toml").write_text(
        "[commands.format]\n"
        'command = ["git", "--version"]\n'
        "\n"
        "[commands.test]\n"
        f"command = {json.dumps(['python', '-c', test_script])}\n",
        encoding="utf-8",
    )
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase 2 integration")
    _git(project, "config", "user.email", "phase2-integration@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: phase 2 integration fixture")
    try:
        yield project
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_real_codemcp_bridge_read_edit_command_and_diff(
    git_project: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(git_project, git_project.parent / "bridge-data")
    app, service = create_app(settings)
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
                        await client.call_tool("project_open", {"project_id": "integration"})
                    )
                    assert opened["status"] == "succeeded"
                    session_id = opened["data"]["session_id"]

                    read = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "path": "src/hello file.txt",
                            },
                        )
                    )
                    assert read["status"] == "succeeded"
                    assert "hello codemcp" in read["data"]["text"]
                    assert service.adapter.is_active("integration") is True

                    searched = _payload(
                        await client.call_tool(
                            "code_search",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "pattern": "hello codemcp",
                            },
                        )
                    )
                    assert searched["status"] == "succeeded"
                    assert "hello file.txt" in searched["data"]["text"]

                    listed = _payload(
                        await client.call_tool(
                            "file_list",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "path": "src",
                            },
                        )
                    )
                    assert listed["status"] == "succeeded"
                    assert "hello file.txt" in listed["data"]["text"]

                    binary = _payload(
                        await client.call_tool(
                            "file_read",
                            {
                                "project_id": "integration",
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
                                "project_id": "integration",
                                "session_id": session_id,
                                "path": "src/large.txt",
                            },
                        )
                    )
                    assert large["error"]["code"] == "FILE_TOO_LARGE"

                    edited = _payload(
                        await client.call_tool(
                            "file_edit",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "path": "src/hello file.txt",
                                "old_string": "hello codemcp",
                                "new_string": "edited codemcp",
                                "description": "test the real Bridge edit path",
                                "client_request_id": "edit-1",
                                "request_hash": request_hash(
                                    _file_edit_input(
                                        "src/hello file.txt",
                                        "hello codemcp",
                                        "edited codemcp",
                                        "test the real Bridge edit path",
                                    )
                                ),
                            },
                        )
                    )
                    assert edited["status"] == "succeeded"
                    assert "src/hello file.txt" in edited["changed_files"]
                    assert (
                        edited["changed_files"]
                        == edited["data"]["checkpoint"]["after"]["changed_files"]
                    )
                    assert "edited codemcp" in (git_project / "src" / "hello file.txt").read_text(
                        encoding="utf-8"
                    )

                    formatted = _payload(
                        await client.call_tool(
                            "format_run",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "command_id": "format",
                                "client_request_id": "format-1",
                                "request_hash": request_hash(
                                    {"command_id": "format", "expected_kind": "format"}
                                ),
                            },
                        )
                    )
                    assert formatted["status"] == "succeeded"
                    assert "git version" in formatted["data"]["text"]

                    tested = _payload(
                        await client.call_tool(
                            "test_run",
                            {
                                "project_id": "integration",
                                "session_id": session_id,
                                "command_id": "test",
                                "client_request_id": "test-1",
                                "request_hash": request_hash(
                                    {"command_id": "test", "expected_kind": "test"}
                                ),
                            },
                        )
                    )
                    assert tested["status"] == "succeeded"
                    assert "Code test successful" in tested["data"]["text"]
                    assert "test passed" in tested["data"]["text"]

                    status = _payload(
                        await client.call_tool(
                            "git_status",
                            {"project_id": "integration", "session_id": session_id},
                        )
                    )
                    assert status["status"] == "succeeded"
                    assert status["data"]["dirty"] is False

                    (git_project / "src" / "notes.txt").write_text(
                        "external change\n", encoding="utf-8"
                    )
                    diff = _payload(
                        await client.call_tool(
                            "git_diff",
                            {"project_id": "integration", "session_id": session_id},
                        )
                    )
                    assert diff["status"] == "succeeded"
                    assert "external change" in diff["data"]["text"]

    await service.close()
    assert service.adapter.is_active("integration") is False
    assert "Stateless session crashed" not in caplog.text
    assert "Attempted to exit a cancel scope" not in caplog.text


@pytest.mark.asyncio
async def test_real_codemcp_bridge_worker_restarts_after_bridge_shutdown(
    git_project: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(git_project, git_project.parent / "bridge-data")

    async def read_once() -> str:
        app, service = create_app(settings)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:46200",
            ) as http:
                async with streamable_http_client(
                    "http://127.0.0.1:46200/mcp",
                    http_client=http,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        opened = _payload(
                            await client.call_tool(
                                "project_open",
                                {"project_id": "integration"},
                            )
                        )
                        assert opened["status"] == "succeeded"
                        session_id = opened["data"]["session_id"]
                        read = _payload(
                            await client.call_tool(
                                "file_read",
                                {
                                    "project_id": "integration",
                                    "session_id": session_id,
                                    "path": "src/hello file.txt",
                                },
                            )
                        )
                        assert read["status"] == "succeeded"
                        assert service.adapter.is_active("integration") is True
                        text = read["data"]["text"]

        await service.close()
        assert service.adapter.is_active("integration") is False
        return text

    first = await read_once()
    second = await read_once()

    assert "hello codemcp" in first
    assert "hello codemcp" in second
    assert "Stateless session crashed" not in caplog.text
    assert "Attempted to exit a cancel scope" not in caplog.text
