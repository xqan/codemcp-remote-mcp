from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_phase556_current_head_windows_installer_acceptance() -> None:
    if os.environ.get("CODEMCP_RUN_RELEASE_INSTALLER_ACCEPTANCE") != "1":
        pytest.skip(
            "set CODEMCP_RUN_RELEASE_INSTALLER_ACCEPTANCE=1 for native release packaging acceptance"
        )
    if os.name != "nt":
        pytest.skip("Phase 5.5.6 installer acceptance runs only on native Windows")

    powershell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.fail("PowerShell 7 is required to build the Windows installer")

    repository_root = Path(__file__).resolve().parents[2]
    script = repository_root / "scripts" / "build-windows-installer.ps1"
    arguments = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
    ]
    iscc_path = os.environ.get("CODEMCP_ISCC_PATH")
    if iscc_path:
        arguments.extend(["-ISCCPath", iscc_path])

    completed = subprocess.run(
        arguments,
        cwd=repository_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "Phase 5.5.6 current-HEAD Windows installer acceptance failed\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert '"status": "ok"' in completed.stdout
    assert '"recommended_transport": "cloudflare"' in completed.stdout
    assert '"smoke": "passed"' in completed.stdout
