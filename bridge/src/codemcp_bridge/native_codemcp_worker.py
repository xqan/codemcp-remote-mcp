"""Native Windows compatibility entry point for the pinned codemcp worker."""

from __future__ import annotations

import asyncio
import os
import sys
import tomllib
from typing import Any

_ORIGINAL_CREATE_SUBPROCESS_EXEC = asyncio.create_subprocess_exec


async def _create_subprocess_exec_with_devnull_stdin(
    program: str,
    *args: str,
    **kwargs: Any,
) -> asyncio.subprocess.Process:
    """Prevent child tools from inheriting the MCP stdio server's stdin on Windows."""

    kwargs.setdefault("stdin", asyncio.subprocess.DEVNULL)
    return await _ORIGINAL_CREATE_SUBPROCESS_EXEC(program, *args, **kwargs)


def _write_file_sync_without_newline_translation(
    file_path: str,
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Write codemcp-normalized newlines without a second Windows text translation."""

    with open(file_path, "w", encoding=encoding, newline="") as handle:
        handle.write(content)


def install_tomli_compatibility(*, modules: dict[str, Any] | None = None) -> None:
    """Use the Python 3.12+ stdlib TOML parser in place of tomli's mypyc wheel."""

    target = sys.modules if modules is None else modules
    target.setdefault("tomli", tomllib)


def install_windows_compatibility(*, os_name: str | None = None) -> bool:
    """Install the narrow compatibility patches required by codemcp 0.3.0 on Windows."""

    effective_os_name = os.name if os_name is None else os_name
    if effective_os_name != "nt":
        return False

    from codemcp.tools import file_utils

    asyncio.create_subprocess_exec = _create_subprocess_exec_with_devnull_stdin
    file_utils.write_file_sync = _write_file_sync_without_newline_translation
    return True


def main() -> None:
    install_tomli_compatibility()
    install_windows_compatibility()
    from codemcp import run as run_codemcp

    run_codemcp()


if __name__ == "__main__":
    main()
