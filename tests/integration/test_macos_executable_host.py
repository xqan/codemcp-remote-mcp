from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="native macOS executable acceptance requires Darwin",
)
def test_macos_candidate_executable_host(tmp_path: Path) -> None:
    if os.environ.get("CODEMCP_RUN_MACOS_PACKAGING_ACCEPTANCE") != "1":
        pytest.skip(
            "set CODEMCP_RUN_MACOS_PACKAGING_ACCEPTANCE=1 for native macOS acceptance"
        )

    candidate_value = os.environ.get("CODEMCP_MACOS_CANDIDATE")
    if not candidate_value:
        pytest.skip(
            "CODEMCP_MACOS_CANDIDATE must point to the extracted codemcp-remote directory"
        )

    candidate = Path(candidate_value).expanduser().resolve()
    executable = candidate / "codemcp-remote"
    cloudflared = candidate / ".codemcp-runtime" / "bin" / "cloudflared"
    assert executable.is_file()
    assert os.access(executable, os.X_OK)
    assert cloudflared.is_file()
    assert os.access(cloudflared, os.X_OK)

    expected_arch = os.environ.get("CODEMCP_EXPECTED_MACOS_ARCH", platform.machine())
    assert platform.machine() == expected_arch

    file_output = subprocess.run(
        ["file", str(executable)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Mach-O" in file_output
    assert (
        expected_arch
        in subprocess.run(
            ["lipo", "-archs", str(executable)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    )

    version = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "0.1.0" in version.stdout

    subprocess.run([str(executable), "check"], check=True)
    subprocess.run(
        [str(executable), "status", "--home", str(tmp_path / "home")], check=True
    )
    subprocess.run([str(cloudflared), "--version"], check=True)

    for script_name in ("codemcp-install.sh", "codemcp-start.sh", "codemcp-stop.sh"):
        script = candidate / script_name
        assert script.is_file()
        assert os.access(script, os.X_OK)
        subprocess.run(["sh", "-n", str(script)], check=True)

    installer_text = (candidate / "codemcp-install.sh").read_text(encoding="utf-8")
    quarantine_cleanup = (
        'xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true'
    )
    assert quarantine_cleanup in installer_text
    assert installer_text.index("SCRIPT_DIR=") < installer_text.index(
        quarantine_cleanup
    )
    assert installer_text.index(quarantine_cleanup) < installer_text.index(
        'CODEMCP="$SCRIPT_DIR/'
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "integration" / "executable_smoke.py"),
            str(executable),
            str(ROOT),
        ],
        check=True,
    )
