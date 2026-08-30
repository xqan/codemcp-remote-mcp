from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from codemcp_bridge.codemcp_probe import (
    CODEMCP_COMMIT,
    CODEMCP_RELEASE,
    ProbeConfig,
    connect,
    extract_chat_id,
    result_text,
)

# Native Windows and WSL2 share one bounded compatibility budget now that the
# worker entry point prevents Git-backed child processes from inheriting MCP stdin.
GIT_SUBTOOL_TIMEOUT_SECONDS = 30.0


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


def _git_status(project: Path) -> str:
    return _git(project, "status", "--porcelain")


def _head(project: Path) -> str:
    return _git(project, "rev-parse", "HEAD")


def _commit_count(project: Path) -> int:
    return int(_git(project, "rev-list", "--count", "HEAD"))


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "phase1 中文 project with spaces"
    source_directory = project / ("nested-" + "x" * 72)
    source_directory.mkdir(parents=True)
    source_file = source_directory / "hello file.txt"
    source_file.write_text("hello codemcp\nsecond line\n", encoding="utf-8")

    command_python = _toml_string(sys.executable)
    command_script = _toml_string(
        "from pathlib import Path; "
        "Path('command-output.txt').write_text('command output\\n', encoding='utf-8')"
    )
    (project / "codemcp.toml").write_text(
        'project_prompt = "PHASE1_PROJECT_PROMPT"\n'
        "\n"
        "[commands.format]\n"
        'command = ["git", "--version"]\n'
        'doc = "show the Git version"\n'
        "\n"
        "[commands.mutate]\n"
        f'command = [{command_python}, "-c", {command_script}]\n'
        'doc = "write a controlled marker"\n'
        "\n"
        "[commands.fail]\n"
        'command = ["git", "--definitely-not-a-codemcp-option"]\n'
        'doc = "return a deterministic failure"\n',
        encoding="utf-8",
    )

    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "codemcp Phase 1")
    _git(project, "config", "user.email", "codemcp-phase1@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: initial project")
    return project


def _probe_config(
    project: Path,
    tmp_path: Path,
    suffix: str = "worker",
    timeout_seconds: float = 30.0,
) -> ProbeConfig:
    return ProbeConfig(
        project_root=project,
        isolated_home=tmp_path / f"codemcp-home-{suffix}",
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_initialize_and_tools_list(git_project: Path, tmp_path: Path) -> None:
    assert CODEMCP_RELEASE == "0.3.0"
    assert CODEMCP_COMMIT == "683e6ec29b15b91ec12430afabf5a45ed57d2489"
    async with connect(_probe_config(git_project, tmp_path)) as connection:
        initialize = connection.initialize_result
        assert initialize.serverInfo.name == "codemcp"
        assert initialize.protocolVersion

        tools_result = await connection.list_tools()
        assert [tool.name for tool in tools_result.tools] == ["codemcp"]

        tool = tools_result.tools[0].model_dump(mode="json", by_alias=True)
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "subtool" in schema["properties"]
        assert {
            "path",
            "content",
            "old_string",
            "new_string",
            "offset",
            "limit",
            "description",
            "pattern",
            "include",
            "command",
            "arguments",
            "chat_id",
            "user_prompt",
            "subject_line",
            "reuse_head_chat_id",
        } <= set(schema["properties"])


@pytest.mark.asyncio
async def test_read_only_subtools(git_project: Path, tmp_path: Path) -> None:
    source_file = next(git_project.rglob("hello file.txt"))
    async with connect(
        _probe_config(
            git_project,
            tmp_path,
            timeout_seconds=GIT_SUBTOOL_TIMEOUT_SECONDS,
        )
    ) as connection:
        ls_result = await connection.call_subtool(
            "LS", path=str(git_project), chat_id="read-only-probe"
        )
        assert not ls_result.isError
        assert "codemcp.toml" in result_text(ls_result)

        read_result = await connection.call_subtool(
            "ReadFile", path=str(source_file), chat_id="read-only-probe"
        )
        assert not read_result.isError
        assert "hello codemcp" in result_text(read_result)

        grep_result = await connection.call_subtool(
            "Grep",
            path=str(git_project),
            pattern="hello codemcp",
            chat_id="read-only-probe",
        )
        assert not grep_result.isError
        assert "hello file.txt" in result_text(grep_result)


@pytest.mark.asyncio
async def test_read_file_subtool(git_project: Path, tmp_path: Path) -> None:
    source_file = next(git_project.rglob("hello file.txt"))
    async with connect(_probe_config(git_project, tmp_path)) as connection:
        read_result = await connection.call_subtool(
            "ReadFile", path=str(source_file), chat_id="read-file-probe"
        )
        assert not read_result.isError
        assert "hello codemcp" in result_text(read_result)


@pytest.mark.asyncio
async def test_format_subtool_is_not_exposed(git_project: Path, tmp_path: Path) -> None:
    async with connect(_probe_config(git_project, tmp_path)) as connection:
        format_result = await connection.call_subtool(
            "Format", path=str(git_project), chat_id="format-probe"
        )
        assert not format_result.isError
        assert "Unknown subtool: Format" in result_text(format_result)


@pytest.mark.asyncio
async def test_subtools_commands_and_git_behavior(
    git_project: Path, tmp_path: Path
) -> None:
    source_file = next(git_project.rglob("hello file.txt"))
    initial_head = _head(git_project)
    initial_count = _commit_count(git_project)

    async with connect(
        _probe_config(
            git_project,
            tmp_path,
            timeout_seconds=GIT_SUBTOOL_TIMEOUT_SECONDS,
        )
    ) as connection:
        init_result = await connection.call_subtool(
            "InitProject",
            path=str(git_project),
            user_prompt="Verify codemcp Phase 1 behavior",
            subject_line="test: codemcp phase 1",
            reuse_head_chat_id=False,
        )
        init_text = result_text(init_result)
        assert not init_result.isError
        assert "PHASE1_PROJECT_PROMPT" in init_text
        assert "format: show the Git version" in init_text
        chat_id = extract_chat_id(init_text)
        assert re.fullmatch(r"\d+-[a-z0-9-]+", chat_id)
        assert _head(git_project) != initial_head
        assert _commit_count(git_project) == initial_count + 1
        assert "codemcp-id: " + chat_id in _git(git_project, "log", "-1", "--pretty=%B")
        assert _git_status(git_project) == ""

        ls_result = await connection.call_subtool(
            "LS", path=str(git_project), chat_id=chat_id
        )
        assert not ls_result.isError
        assert "codemcp.toml" in result_text(ls_result)

        read_result = await connection.call_subtool(
            "ReadFile", path=str(source_file), chat_id=chat_id
        )
        assert not read_result.isError
        assert "hello codemcp" in result_text(read_result)

        grep_result = await connection.call_subtool(
            "Grep",
            path=str(git_project),
            pattern="hello codemcp",
            chat_id=chat_id,
        )
        assert not grep_result.isError
        assert "hello file.txt" in result_text(grep_result)

        head_before_edit = _head(git_project)
        count_before_edit = _commit_count(git_project)
        edit_result = await connection.call_subtool(
            "EditFile",
            path=str(source_file),
            old_string="hello codemcp",
            new_string="edited codemcp",
            description="edit the Phase 1 fixture",
            chat_id=chat_id,
        )
        assert not edit_result.isError
        assert "Successfully edited" in result_text(edit_result)
        assert source_file.read_text(encoding="utf-8").startswith("edited codemcp")
        assert _head(git_project) != head_before_edit
        assert _commit_count(git_project) == count_before_edit
        assert _git_status(git_project) == ""

        head_before_write = _head(git_project)
        write_result = await connection.call_subtool(
            "WriteFile",
            path=str(source_file),
            content="written by codemcp\n",
            description="write the Phase 1 fixture",
            chat_id=chat_id,
        )
        assert not write_result.isError
        assert "Successfully wrote" in result_text(write_result)
        assert source_file.read_text(encoding="utf-8") == "written by codemcp\n"
        assert _head(git_project) != head_before_write
        assert _commit_count(git_project) == count_before_edit
        assert _git_status(git_project) == ""

        head_before_failed_edit = _head(git_project)
        failed_edit = await connection.call_subtool(
            "EditFile",
            path=str(source_file),
            old_string="not present",
            new_string="must not be written",
            description="expected failed edit",
            chat_id=chat_id,
        )
        assert not failed_edit.isError
        assert "String to replace not found" in result_text(failed_edit)
        assert _head(git_project) == head_before_failed_edit
        assert source_file.read_text(encoding="utf-8") == "written by codemcp\n"
        assert _git_status(git_project) == ""

        format_result = await connection.call_subtool(
            "RunCommand", path=str(git_project), command="format", chat_id=chat_id
        )
        assert not format_result.isError
        assert "Code format successful" in result_text(format_result)
        assert "git version" in result_text(format_result)

        command_result = await connection.call_subtool(
            "RunCommand", path=str(git_project), command="mutate", chat_id=chat_id
        )
        assert not command_result.isError
        assert "Code mutate successful" in result_text(command_result)
        assert (git_project / "command-output.txt").read_text(encoding="utf-8") == (
            "command output\n"
        )

        failed_command = await connection.call_subtool(
            "RunCommand", path=str(git_project), command="fail", chat_id=chat_id
        )
        assert not failed_command.isError
        failed_text = result_text(failed_command)
        assert "command failed with exit code" in failed_text
        assert "STDERR:" in failed_text

        unsupported_format = await connection.call_subtool(
            "Format", path=str(git_project), chat_id=chat_id
        )
        assert not unsupported_format.isError
        assert "Unknown subtool: Format" in result_text(unsupported_format)


@pytest.mark.asyncio
async def test_stdio_worker_restart_and_duplicate_startup(
    git_project: Path, tmp_path: Path
) -> None:
    first_config = _probe_config(git_project, tmp_path, "first")
    second_config = _probe_config(git_project, tmp_path, "second")

    async with connect(first_config) as first:
        first_tools = await first.list_tools()
        assert first_tools.tools[0].name == "codemcp"
        async with connect(second_config) as second:
            second_tools = await second.list_tools()
            assert second_tools.tools[0].name == "codemcp"

    # The first worker has exited with the context; a fresh worker must still
    # initialize successfully.  stdio has no listening port, so port-collision
    # behavior is not applicable to this transport.
    async with connect(_probe_config(git_project, tmp_path, "restart")) as restarted:
        assert (await restarted.list_tools()).tools[0].name == "codemcp"
