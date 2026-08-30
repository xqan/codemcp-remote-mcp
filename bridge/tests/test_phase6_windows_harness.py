from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts" / "validate-phase6-windows.ps1"
RC_SCRIPT = ROOT / "scripts" / "prepare-windows-release-candidate.ps1"
BUILD_SCRIPT = ROOT / "scripts" / "build-windows-exe.ps1"
BUILD_RELEASE_SCRIPT = ROOT / "scripts" / "build-windows-release.ps1"


def test_phase6_harness_covers_packaged_lifecycle_and_failure_cases() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "[int]$Iterations = 20" in script
    assert "[string]$RuntimeHome" in script
    assert "[string]$Home" not in script
    assert "[int]$Pid" not in script
    assert "[int]$ProcessId" in script
    assert "Assert-ProcessExited -Pid" not in script
    assert "Assert-ProcessExited -ProcessId $bridgePid" in script
    assert "Assert-ProcessExited -ProcessId $tunnelPid" in script
    assert '$RuntimeArguments = @("--home", $RuntimeHome)' in script
    assert 'status = "phase6-local-host-gate-pass"' in script
    assert "requested_iterations = $Iterations" in script
    assert "failed_iterations = 0" in script
    assert "Stop-Process -Id $bridgePid -Force" in script
    assert "Stop-Process -Id $tunnelPid -Force" in script
    assert "Assert-StartFailsWithListener -Port $BridgePort" in script
    assert "Assert-StartFailsWithListener -Port $MetricsPort" in script
    assert "doctor unexpectedly found Git after PATH isolation" in script
    assert "cloudflare-tunnel-token.dpapi" in script
    assert 'plaintext_log_secret_shape_scan = "pass"' in script


def test_phase6_harness_does_not_claim_full_phase6_pass() -> None:
    script = HARNESS.read_text(encoding="utf-8")

    assert "remaining_remote_cases = @(" in script
    assert "native worker abnormal exit during controlled mutation" in script
    assert "tunnel disconnect during mutation" in script
    assert "Windows path and encoding matrix through the MCP contract" in script
    assert "does not claim full Phase 6 PASS" in script


def test_release_candidate_includes_phase6_harness() -> None:
    script = RC_SCRIPT.read_text(encoding="utf-8")

    assert (
        '$phase6ValidationScript = Join-Path $repositoryRoot "scripts\\validate-phase6-windows.ps1"'
        in script
    )
    assert "Copy-Item -LiteralPath $phase6ValidationScript" in script
    assert '"validate-phase6-windows.ps1"' in script


def test_windows_build_provenance_records_clean_source_commit() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "rev-parse HEAD" in script
    assert "rev-parse --abbrev-ref HEAD" in script
    assert "status --porcelain" in script
    assert "Windows release build requires a clean source worktree" in script
    assert "git_commit = $sourceCommit" in script
    assert "git_branch = $sourceBranch" in script
    assert "worktree_dirty = $false" in script


def test_release_wrapper_rejects_stale_installer_provenance() -> None:
    script = BUILD_RELEASE_SCRIPT.read_text(encoding="utf-8")

    assert 'Join-Path $stagingPayload "BUILD_PROVENANCE.json"' in script
    assert 'Join-Path $stagingPayload "SOURCE_COMMIT.txt"' in script
    assert "staging SOURCE_COMMIT.txt is invalid" in script
    assert "staging payload source commit does not match the current release commit" in script
    assert "ConvertFrom-Json" not in script
    assert "source_git_commit = $sourceCommit" in script


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser check runs on native Windows")
def test_phase6_harness_parses_as_powershell() -> None:
    powershell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is unavailable")

    escaped = str(HARNESS).replace("'", "''")
    parser_command = (
        f"$path='{escaped}'; "
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$path,[ref]$tokens,[ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            parser_command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
