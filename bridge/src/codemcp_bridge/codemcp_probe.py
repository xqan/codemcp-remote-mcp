"""Small MCP compatibility probe for the pinned codemcp release.

This module is intentionally limited to starting codemcp over stdio and
exercising its MCP contract.  It is not the production Bridge adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CODEMCP_RELEASE = "0.3.0"
CODEMCP_COMMIT = "683e6ec29b15b91ec12430afabf5a45ed57d2489"
CODEMCP_TOOL = "codemcp"


@dataclass(frozen=True)
class ProbeConfig:
    """Runtime settings for one isolated codemcp worker."""

    project_root: Path
    isolated_home: Path
    command: tuple[str, ...] = field(
        default_factory=lambda: (sys.executable, "-m", "codemcp_bridge.native_codemcp_worker")
    )
    worker_cwd: Path | None = None
    timeout_seconds: float = 30.0


@dataclass
class ProbeConnection:
    """Initialized MCP session plus the captured worker diagnostics."""

    session: ClientSession
    initialize_result: Any
    _server_stderr: TextIO
    timeout_seconds: float

    @property
    def server_stderr(self) -> str:
        """Return stderr emitted by the codemcp worker so probes can record it."""

        self._server_stderr.flush()
        position = self._server_stderr.tell()
        self._server_stderr.seek(0)
        content = self._server_stderr.read()
        self._server_stderr.seek(position)
        return content

    async def list_tools(self) -> Any:
        """Return the live ``tools/list`` response."""

        async with asyncio.timeout(self.timeout_seconds):
            return await self.session.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call one live MCP tool with a bounded timeout."""

        async with asyncio.timeout(self.timeout_seconds):
            return await self.session.call_tool(name, arguments=arguments)

    async def call_subtool(self, subtool: str, **arguments: Any) -> Any:
        """Call a codemcp subtool through its single MCP tool."""

        return await self.call_tool(CODEMCP_TOOL, {"subtool": subtool, **arguments})


def _worker_environment(config: ProbeConfig) -> dict[str, str]:
    """Build a child environment with codemcp's home redirected to a fixture."""

    config.isolated_home.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"

    # codemcp 0.3.0 hard-codes Path.home()/~/.codemcp for its log file.  A
    # process-local home is required for repeatable tests and avoids touching
    # the operator's profile.  Windows' expanduser uses USERPROFILE; POSIX
    # uses HOME.
    if os.name == "nt":
        environment["USERPROFILE"] = str(config.isolated_home)
    else:
        environment["HOME"] = str(config.isolated_home)

    return environment


@asynccontextmanager
async def connect(config: ProbeConfig) -> AsyncIterator[ProbeConnection]:
    """Start one codemcp stdio worker and initialize its MCP session."""

    if not config.project_root.is_dir():
        raise ValueError(f"project root does not exist: {config.project_root}")
    if not config.command:
        raise ValueError("codemcp command must not be empty")

    server = StdioServerParameters(
        command=config.command[0],
        args=list(config.command[1:]),
        env=_worker_environment(config),
        cwd=str(config.worker_cwd) if config.worker_cwd is not None else None,
    )
    server_stderr_path = config.isolated_home / "worker.stderr.log"
    server_stderr = server_stderr_path.open("w+", encoding="utf-8")
    try:
        async with stdio_client(server, errlog=server_stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                async with asyncio.timeout(config.timeout_seconds):
                    initialize_result = await session.initialize()
                yield ProbeConnection(
                    session,
                    initialize_result,
                    server_stderr,
                    config.timeout_seconds,
                )
    finally:
        server_stderr.close()


def model_dump(value: Any) -> Any:
    """Serialize MCP/Pydantic objects without coupling the probe to internals."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [model_dump(item) for item in value]
    if isinstance(value, tuple):
        return [model_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: model_dump(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def result_text(result: Any) -> str:
    """Join text blocks from an MCP tool result."""

    return "\n".join(
        block.text for block in getattr(result, "content", []) if hasattr(block, "text")
    )


def extract_chat_id(text: str) -> str:
    """Extract the chat id assigned by InitProject from its returned prompt."""

    match = re.search(r"This chat has been assigned a chat ID: ([^\r\n]+)", text)
    if match is None:
        raise ValueError("InitProject response did not contain a chat id")
    return match.group(1).strip()


async def inspect_server(config: ProbeConfig) -> dict[str, Any]:
    """Return a JSON-serializable snapshot of initialize and tools/list."""

    async with connect(config) as connection:
        tools_result = await connection.list_tools()
        return {
            "release": CODEMCP_RELEASE,
            "commit": CODEMCP_COMMIT,
            "initialize": model_dump(connection.initialize_result),
            "tools": model_dump(tools_result),
            "server_stderr": connection.server_stderr,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the pinned codemcp MCP server")
    parser.add_argument("project_root", type=Path)
    parser.add_argument(
        "--home",
        dest="isolated_home",
        type=Path,
        default=Path.cwd() / ".local" / "codemcp-probe-home",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = asyncio.run(
        inspect_server(
            ProbeConfig(
                project_root=args.project_root,
                isolated_home=args.isolated_home,
            )
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
