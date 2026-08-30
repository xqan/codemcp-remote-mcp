from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _windows_path(path: Path) -> str:
    if os.name == "nt":
        return str(path)
    completed = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def test_windows_onedir_executable_build_and_worker_smoke() -> None:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
    else:
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if shutil.which("wslpath") is None:
            pytest.skip("Windows interoperability is unavailable on this host")
    if powershell is None:
        pytest.skip("PowerShell is unavailable on this host")

    repository_root = Path(__file__).resolve().parents[2]
    windows_root = _windows_path(repository_root).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        f"Set-Location -LiteralPath '{windows_root}'; "
        "& .\\scripts\\build-windows-exe.ps1 | Out-Host; "
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    )
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Windows EXE acceptance failed\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert "frozen worker smoke: PASS" in completed.stdout
