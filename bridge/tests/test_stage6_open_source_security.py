from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from codemcp_bridge.project_profiles import get_builtin_profile

GITLEAKS_VERSION = "8.30.0"
GITLEAKS_WINDOWS_X64_SHA256 = "54fe94f644b832dd08e8c3a5915efb3bfa862386d59fb27ca0792cb687a83573"
GITLEAKS_LINUX_X64_SHA256 = "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e"


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_root() / path).read_text(encoding="utf-8")


def test_local_stage6_audit_uses_pinned_gitleaks_and_locked_uv_audit() -> None:
    prepare = _read("scripts/prepare-gitleaks.ps1")
    audit = _read("scripts/validate-open-source-security.ps1")

    assert GITLEAKS_VERSION in prepare
    assert GITLEAKS_WINDOWS_X64_SHA256 in prepare
    assert "Gitleaks archive checksum mismatch" in prepare
    assert "uv.Source audit --project" in audit
    assert "--frozen" in audit
    assert "dependency_license_audit.py" in audit
    assert "dependency license evidence audit failed" in audit
    assert "& $gitleaks git" in audit
    assert "& $gitleaks dir" in audit
    assert "--redact=100" in audit
    assert "--log-opts=--all" in audit
    assert "git archive" in audit
    assert "projects.toml" in audit
    assert "*.dpapi" in audit
    assert "*.sqlite3*" in audit
    assert "operator-specific deployment/path data" in audit
    assert ".local\\release-candidate\\codemcp-remote-v0.1.0-windows-x64.zip" in audit


def test_codemcp_remote_profile_exposes_fixed_security_audits_only() -> None:
    profile = get_builtin_profile("codemcp-remote")
    assert profile is not None

    source_command = profile.commands["security-audit"]
    assert source_command.kind == "verify"
    assert source_command.approval == "not-required"
    assert source_command.argv == (
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        "scripts/validate-open-source-security.ps1",
    )

    artifact_command = profile.commands["artifact-audit"]
    assert artifact_command.kind == "verify"
    assert artifact_command.approval == "not-required"
    assert artifact_command.argv == (
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        "scripts/validate-open-source-security.ps1",
        "-RequireArtifact",
    )


def test_ci_has_dependency_current_tree_and_full_history_security_gates() -> None:
    workflow = _read(".github/workflows/ci.yml")

    assert "security:" in workflow
    assert "fetch-depth: 0" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0" in workflow
    assert "actions/setup-python@v7.0.0" not in workflow
    assert "uv audit --project bridge --frozen" in workflow
    assert "Inventory dependency license evidence" in workflow
    assert "dependency_license_audit.py --lock bridge/uv.lock" in workflow
    assert (
        "ruff check bridge/src bridge/tests tests/integration scripts/dependency_license_audit.py"
        in workflow
    )
    assert (
        "ruff format --check bridge/src bridge/tests tests/integration "
        "scripts/dependency_license_audit.py"
    ) in workflow
    assert f'GITLEAKS_VERSION: "{GITLEAKS_VERSION}"' in workflow
    assert f'GITLEAKS_SHA256: "{GITLEAKS_LINUX_X64_SHA256}"' in workflow
    assert '"$RUNNER_TEMP/gitleaks" dir --redact=100 --no-banner' in workflow
    assert '"$RUNNER_TEMP/gitleaks" git --redact=100 --no-banner' in workflow
    assert "--log-opts=--all ." in workflow


def test_dependency_license_inventory_runs_in_locked_test_environment(tmp_path: Path) -> None:
    root = _root()
    report_path = tmp_path / "dependency-licenses.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "dependency_license_audit.py"),
            "--lock",
            str(root / "bridge" / "uv.lock"),
            "--output",
            str(report_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["installed_locked_package_count"] > 0
    assert report["missing_license_evidence"] == []
    assert report["codemcp_validation_errors"] == []
    assert report["manual_compatibility_review_required"] is True

    codemcp = next(item for item in report["packages"] if item["name"] == "codemcp")
    assert codemcp["version"] == "0.3.0"
    assert codemcp["license_field"] == "MIT"
    assert "Apache-2.0" in codemcp["known_metadata_discrepancy"]
