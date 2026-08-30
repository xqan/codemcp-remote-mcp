from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codemcp_bridge.mcp_server import create_app
from codemcp_bridge.operation_service import request_hash
from codemcp_bridge.project_readiness import inspect_development_readiness
from codemcp_bridge.settings import load_settings


def _git(project: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (completed.stdout or "").strip()


def _payload(result: Any) -> dict[str, Any]:
    if result.structuredContent:
        return result.structuredContent
    text_blocks = [block.text for block in result.content if hasattr(block, "text")]
    return json.loads("\n".join(text_blocks))


@pytest.mark.asyncio
async def test_root_only_maven_profile_runs_doctor_compile_and_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "maven project"
    project.mkdir()
    (project / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>example</groupId><artifactId>demo</artifactId><version>1</version></project>\n",
        encoding="utf-8",
    )
    _git(project, "init", "-b", "develop")
    _git(project, "config", "user.name", "Maven acceptance")
    _git(project, "config", "user.email", "maven-acceptance@example.invalid")
    _git(project, "add", "pom.xml")
    _git(project, "commit", "-m", "test: maven profile fixture")
    initial_head = _git(project, "rev-parse", "HEAD")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_maven = bin_dir / ("mvn.cmd" if os.name == "nt" else "mvn")
    fake_maven.write_text(
        "@echo off\necho FAKE_MVN:%*\n"
        if os.name == "nt"
        else "#!/bin/sh\nprintf 'FAKE_MVN:%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        fake_maven.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[server]\n"
        'host = "127.0.0.1"\n'
        "port = 46200\n"
        'path = "/mcp"\n'
        'transport = "streamable-http"\n'
        "\n"
        "[storage]\n"
        'data_dir = "../bridge-data"\n'
        'sqlite_file = "../bridge-data/bridge.sqlite3"\n'
        'log_dir = "../bridge-data/logs"\n'
        "\n"
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n"
        'mutation_lock = "per-project"\n'
        "\n"
        "[codemcp]\n"
        'worker_mode = "local"\n'
        "startup_timeout_seconds = 30\n"
        "worker_timeout_seconds = 60\n"
        "shutdown_timeout_seconds = 5\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        f"[projects.maven-demo]\nroot = {json.dumps(project.as_posix())}\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
    spec = settings.projects["maven-demo"]
    assert spec.profile == "java-maven"
    assert spec.profile_source == "detected"
    assert set(spec.commands) == {"doctor", "compile", "test", "verify", "build"}

    readiness = inspect_development_readiness(spec)
    assert readiness.codemcp_config_source == "generated"
    assert readiness.codemcp_config_ready is True
    assert readiness.development_ready is True
    assert not (project / "codemcp.toml").exists()

    app, _service = create_app(settings)
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
                        await client.call_tool("project_open", {"project_id": "maven-demo"})
                    )
                    assert opened["status"] == "succeeded"
                    session_id = opened["data"]["session_id"]

                    status = _payload(
                        await client.call_tool(
                            "project_status",
                            {"project_id": "maven-demo", "session_id": session_id},
                        )
                    )
                    assert status["data"]["profile"] == "java-maven"
                    assert status["data"]["codemcp_config_source"] == "generated"
                    assert status["data"]["development_ready"] is True

                    expected_outputs = {
                        "doctor": "--version",
                        "compile": "-DskipTests compile",
                        "test": "test",
                    }
                    for command_id, expected_output in expected_outputs.items():
                        result = _payload(
                            await client.call_tool(
                                "registered_command_run",
                                {
                                    "project_id": "maven-demo",
                                    "session_id": session_id,
                                    "command_id": command_id,
                                    "client_request_id": f"maven-{command_id}",
                                    "request_hash": request_hash({"command_id": command_id}),
                                },
                            )
                        )
                        assert result["status"] == "succeeded"
                        assert f"FAKE_MVN:{expected_output}" in result["data"]["text"]
                        assert not (project / "codemcp.toml").exists()
                        assert _git(project, "status", "--porcelain") == ""
                        assert _git(project, "rev-parse", "HEAD") == initial_head
