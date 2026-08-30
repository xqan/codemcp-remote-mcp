from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_HEALTH_URL = "http://127.0.0.1:46200/healthz"


def _live_bridge_available() -> bool:
    try:
        with urllib.request.urlopen(BRIDGE_HEALTH_URL, timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


@pytest.mark.skipif(os.name != "nt", reason="Phase 6 live-host smoke is Windows-only")
def test_phase6_live_host_doctor_and_stop_dry_run() -> None:
    if not _live_bridge_available():
        pytest.skip("no live loopback Bridge is available on the Phase 6 baseline port")

    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is unavailable for the source-mode Phase 6 smoke")

    doctor = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(REPOSITORY_ROOT / "scripts" / "doctor.ps1"),
            "-SkipTunnel",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert doctor.returncode == 0, doctor.stdout + "\n" + doctor.stderr
    doctor_payload = json.loads(doctor.stdout)
    assert doctor_payload["status"] == "ok"
    assert doctor_payload["tunnel"]["status"] == "skipped"

    dry_run = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(REPOSITORY_ROOT / "scripts" / "stop-all.ps1"),
            "-WhatIf",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stdout + "\n" + dry_run.stderr
    dry_run_payload = json.loads(dry_run.stdout)
    assert dry_run_payload["status"] == "dry_run"
