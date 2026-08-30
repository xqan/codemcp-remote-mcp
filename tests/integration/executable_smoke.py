from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _git(project: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (completed.stdout or "").strip()


def _result_text(result: object) -> str:
    return "\n".join(
        block.text
        for block in getattr(result, "content", [])
        if isinstance(getattr(block, "text", None), str)
    )


async def _run(executable: Path, repository_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="codemcp-exe-smoke-") as temporary:
        temp_root = Path(temporary)
        home = temp_root / "home"
        project = temp_root / "phase2 中文 project with spaces"
        home.mkdir()
        project.mkdir()

        source_file = project / "hello.txt"
        source_file.write_text("hello frozen worker\n", encoding="utf-8")
        (project / "codemcp.toml").write_text(
            'project_prompt = "EXE_SMOKE_PROMPT"\n',
            encoding="utf-8",
        )
        _git(project, "init", "-b", "main")
        _git(project, "config", "user.name", "codemcp Phase 2")
        _git(project, "config", "user.email", "codemcp-phase2@example.invalid")
        _git(project, "add", ".")
        _git(project, "commit", "-m", "test: executable smoke fixture")

        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["HOME"] = str(home)
        environment["USERPROFILE"] = str(home)

        server = StdioServerParameters(
            command=str(executable),
            args=["_worker"],
            env=environment,
            cwd=str(project),
        )
        async with (
            stdio_client(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await asyncio.wait_for(session.initialize(), timeout=30)
            assert initialized.serverInfo.name == "codemcp"

            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            assert [tool.name for tool in tools.tools] == ["codemcp"]

            init_result = await asyncio.wait_for(
                session.call_tool(
                    "codemcp",
                    arguments={
                        "subtool": "InitProject",
                        "path": str(project),
                        "user_prompt": "Validate frozen worker mutation behavior",
                        "subject_line": "test: frozen worker smoke",
                        "reuse_head_chat_id": False,
                    },
                ),
                timeout=30,
            )
            assert not init_result.isError
            init_text = _result_text(init_result)
            assert "EXE_SMOKE_PROMPT" in init_text
            match = re.search(
                r"This chat has been assigned a chat ID: ([^\r\n]+)", init_text
            )
            assert match is not None
            chat_id = match.group(1).strip()

            read_result = await asyncio.wait_for(
                session.call_tool(
                    "codemcp",
                    arguments={
                        "subtool": "ReadFile",
                        "path": str(source_file),
                        "chat_id": chat_id,
                    },
                ),
                timeout=30,
            )
            assert not read_result.isError
            assert "hello frozen worker" in _result_text(read_result)

            edit_result = await asyncio.wait_for(
                session.call_tool(
                    "codemcp",
                    arguments={
                        "subtool": "EditFile",
                        "path": str(source_file),
                        "old_string": "hello frozen worker",
                        "new_string": "edited by frozen worker",
                        "description": "verify frozen EXE mutation",
                        "chat_id": chat_id,
                    },
                ),
                timeout=30,
            )
            assert not edit_result.isError
            assert "Successfully edited" in _result_text(edit_result)
            assert source_file.read_text(encoding="utf-8").startswith(
                "edited by frozen worker"
            )
            assert _git(project, "status", "--porcelain") == ""

        assert repository_root.is_dir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("repository_root", type=Path)
    args = parser.parse_args()

    executable = args.executable.resolve()
    repository_root = args.repository_root.resolve()
    if not executable.is_file():
        raise SystemExit(f"missing executable: {executable}")
    if not repository_root.is_dir():
        raise SystemExit(f"missing repository root: {repository_root}")

    asyncio.run(_run(executable, repository_root))
    print("frozen worker smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
