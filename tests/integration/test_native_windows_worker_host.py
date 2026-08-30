from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _windows_path(path: Path) -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


@pytest.mark.skipif(
    os.name == "nt", reason="WSL host launcher is only needed outside native Windows"
)
def test_native_windows_worker_matrix_from_wsl() -> None:
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if powershell is None or shutil.which("wslpath") is None:
        pytest.skip("Windows interoperability is unavailable on this host")

    repository_root = Path(__file__).resolve().parents[2]
    windows_root = _windows_path(repository_root).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"Set-Location -LiteralPath '{windows_root}'; "
        "uv run --project bridge pytest -q "
        "bridge/tests/test_phase2_worker.py "
        "tests/integration/test_codemcp_compatibility.py"
    )
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "native Windows compatibility matrix failed\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )
