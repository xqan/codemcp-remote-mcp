from __future__ import annotations

from pathlib import Path

from codemcp_bridge.transports.cloudflare import (
    BUNDLED_WINDOWS_AMD64_SHA256,
    BUNDLED_WINDOWS_AMD64_VERSION,
)

EXPECTED_CLOUDFLARED_VERSION = "2026.7.3"
EXPECTED_CLOUDFLARED_SHA256 = "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841"


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script(name: str) -> str:
    return (_repository_root() / "scripts" / name).read_text(encoding="utf-8")


def test_cloudflared_packaging_pin_matches_runtime_provider() -> None:
    script = _script("prepare-cloudflared.ps1")

    assert BUNDLED_WINDOWS_AMD64_VERSION == EXPECTED_CLOUDFLARED_VERSION
    assert BUNDLED_WINDOWS_AMD64_SHA256 == EXPECTED_CLOUDFLARED_SHA256
    assert f'"{EXPECTED_CLOUDFLARED_VERSION}" = "{EXPECTED_CLOUDFLARED_SHA256}"' in script
    assert "cloudflared-windows-amd64.exe" in script
    assert "cloudflare/cloudflared/releases/download" in script
    assert 'Join-Path $DestinationDir "cloudflared.exe"' in script
    assert "THIRD_PARTY\\cloudflared" in script
    assert "Apache License 2.0" in script


def test_windows_exe_build_uses_sha256_verified_pyinstaller_dependency_closure() -> None:
    build = _script("build-windows-exe.ps1")
    helper = _script("prepare-pypi-wheel.ps1")

    expected_artifacts = {
        "pyinstaller-6.22.2-py3-none-win_amd64.whl": (
            "9b990fa6bbe143572f06644a984ad0d7aa2e2ccc6929d4916031343a5888e9a7"
        ),
        "pyinstaller_hooks_contrib-2026.6-py3-none-any.whl": (
            "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3"
        ),
        "altgraph-0.17.5-py2.py3-none-any.whl": (
            "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597"
        ),
        "pefile-2024.8.26-py3-none-any.whl": (
            "76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f"
        ),
        "pywin32_ctypes-0.2.3-py3-none-any.whl": (
            "8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8"
        ),
        "packaging-26.3-py3-none-any.whl": (
            "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"
        ),
        "setuptools-84.0.0-py3-none-any.whl": (
            "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"
        ),
    }
    for filename, sha256 in expected_artifacts.items():
        assert filename in build
        assert sha256 in build

    assert "prepare-pypi-wheel.ps1" in build
    assert '"--with", $pyInstallerWheel' in build
    assert '"--with", $setuptoolsWheel' in build
    assert "pypi.org/pypi/{0}/{1}/json" in helper
    assert "PyPI published SHA-256 does not match the repository pin" in helper
    assert "cached PyPI wheel checksum mismatch" in helper
    assert "downloaded PyPI wheel checksum mismatch" in helper
    assert 'Join-Path $appDir "THIRD_PARTY\\pyinstaller"' in build
    assert "GPL-2.0-or-later WITH Bootloader-exception" in build
    assert 'Join-Path $appDir "BUILD_PROVENANCE.json"' in build
    assert 'schema = "codemcp-remote-build-provenance-v1"' in build


def test_provider_neutral_packaging_stages_both_remote_transports() -> None:
    script = _script("prepare-remote-transport.ps1")

    assert "prepare-cloudflared.ps1" in script
    assert "prepare-tunnel-client.ps1" in script
    assert 'recommended_provider = "cloudflare"' in script
    assert 'codemcpVersion = "0.3.0"' in script
    assert "a56123f6e1544aed55dbfd1b4946fc2583222b4104a82d8a2171d8c1621cd32a" in script
    assert "a28161aa86176cebd1861e7c134ac98ab1762849d75b46915e0a9fc4ef6efae7" in script
    assert "Distribution metadata License field: MIT" in script
    assert "Bundled License-File: Apache License 2.0" in script
    assert "THIRD_PARTY\\codemcp\\LICENSE.txt" in script
    assert 'Join-Path $codemcpThirdPartyDir "NOTICE.txt"' in script
    assert "THIRD_PARTY\\cloudflared\\LICENSE" in script
    assert "THIRD_PARTY\\tunnel-client\\LICENSE" in script
    assert "THIRD_PARTY\\pyinstaller\\NOTICE.txt" in script
    assert "THIRD_PARTY\\pyinstaller\\COPYING.txt" in script
    assert "BUILD_PROVENANCE.json" in script
    assert "GPL-2\\.0-or-later WITH Bootloader-exception" in script
    assert "THIRD_PARTY_NOTICES.txt" in script


def test_installer_build_rejects_secrets_and_smokes_upgrade_preservation() -> None:
    script = _script("build-windows-installer.ps1")

    assert "prepare-remote-transport.ps1" in script
    assert '"*.dpapi"' in script
    assert 'Join-Path $appDir "config\\remote.toml"' in script
    assert 'Join-Path $appDir "config\\tunnel.env"' in script
    assert 'Join-Path $installedLocation "cloudflared.exe"' in script
    assert 'Join-Path $installedLocation "codemcp-start.cmd"' in script
    assert 'Join-Path $installedLocation "codemcp-stop.cmd"' in script
    assert 'Join-Path $installedLocation "BUILD_PROVENANCE.json"' in script
    assert 'Join-Path $installedLocation "THIRD_PARTY\\pyinstaller\\COPYING.txt"' in script
    assert 'Join-Path $installedLocation "THIRD_PARTY\\pyinstaller\\NOTICE.txt"' in script
    assert 'Join-Path $installedLocation "THIRD_PARTY\\codemcp\\LICENSE.txt"' in script
    assert 'Join-Path $installedLocation "THIRD_PARTY\\codemcp\\NOTICE.txt"' in script
    assert "installed cloudflared checksum differs from the verified staging payload" in script
    assert "silent installer upgrade smoke failed" in script
    assert "installer upgrade removed user runtime data" in script
    assert "silent uninstall removed user runtime data" in script
    assert "staging_payload = $appDir" in script


def test_installer_smoke_can_isolate_from_existing_production_install() -> None:
    script = _script("build-windows-installer.ps1")
    installer = _script("codemcp-remote.iss")

    assert "#ifndef InstallerAppId" in installer
    assert "AppId={#InstallerAppId}" in installer
    assert "#ifndef ProductRegistryKey" in installer
    assert "ProductRegistryKey = '{#ProductRegistryKey}';" in installer
    assert '$smokeMode = "isolated-existing-production-install"' in script
    assert '"/DInstallerAppName=codemcp-remote Installer Smoke"' in script
    assert '"/DInstallerGroupName=codemcp-remote Installer Smoke"' in script
    assert '("/DProductRegistryKey={0}" -f $smokeProductRegistryKey)' in script
    assert "-FilePath $smokeInstallerPath" in script
    assert "isolated installer smoke removed the production uninstall registration" in script
    assert "isolated installer smoke changed the production InstallLocation" in script
    assert "smoke_mode = $smokeMode" in script


def test_one_click_release_orchestrator_audits_payload_and_final_rc() -> None:
    script = _script("build-windows-release.ps1")

    assert "build-windows-installer.ps1" in script
    assert '"Staging payload security audit"' in script
    assert 'Save-SecurityEvidence -Name "staging-payload"' in script
    assert "prepare-windows-release-candidate.ps1" in script
    assert '"Final RC security audit"' in script
    assert '"-ArtifactPath", $candidateZip, "-RequireArtifact"' in script
    assert 'Save-SecurityEvidence -Name "final-rc"' in script
    assert "installer_smoke_mode = $installerSmokeMode" in script
    assert '"pending-clean-machine"' in script
    assert 'next_gate = "clean-machine-validation"' in script
    assert "Invoke-ReleaseScript" in script
    assert "ConvertFrom-Json" not in script
    assert 'Copy-Item -Path (Join-Path $source "*")' in script
    assert "-SkipSmoke" not in script


def test_release_manifest_is_cloudflare_first_and_external_auth_is_not_bundled() -> None:
    script = _script("prepare-windows-release-candidate.ps1")

    assert 'recommended_transport = "cloudflare"' in script
    assert 'executable = "cloudflared.exe"' in script
    assert f'version = "{EXPECTED_CLOUDFLARED_VERSION}"' in script
    assert f'sha256 = "{EXPECTED_CLOUDFLARED_SHA256}"' in script
    assert (
        "compatible external mcp-auth-server deployment implementing mcp-rs-verification-v1"
        in script
    )
    assert "local or bundled mcp-auth-server runtime" in script
    assert "docs\\guides\\clean-machine-validation.md" in script
    assert "docs\\releases\\v0.1.0\\packaging-phase-5-clean-machine-validation.md" not in script


def test_packaged_windows_payload_includes_one_click_lifecycle_scripts() -> None:
    build_script = _script("build-windows-exe.ps1")
    installer = _script("codemcp-remote.iss")
    start_script = _script("codemcp-start.cmd")
    stop_script = _script("codemcp-stop.cmd")

    assert "scripts\\codemcp-start.cmd" in build_script
    assert "scripts\\codemcp-stop.cmd" in build_script
    assert '"%~dp0codemcp-remote.exe" start' in start_script
    assert '"%~dp0codemcp-remote.exe" stop' in stop_script
    assert "--home" not in start_script
    assert "--home" not in stop_script
    assert "Start codemcp-remote" in installer
    assert "Stop codemcp-remote" in installer
    assert 'Filename: "{app}\\codemcp-start.cmd"' in installer
    assert 'Filename: "{app}\\codemcp-stop.cmd"' in installer


def test_inno_uninstall_does_not_target_user_runtime_data() -> None:
    script = _script("codemcp-remote.iss")

    assert "[UninstallDelete]" not in script
    assert 'Source: "{#SourceDir}\\*"; DestDir: "{app}"' in script
    assert 'codemcp-remote.exe"; Parameters: "stop"' in script
