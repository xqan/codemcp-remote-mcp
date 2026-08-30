from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _script() -> str:
    return (
        Path(__file__).resolve().parents[2] / "scripts" / "validate-clean-windows-release.ps1"
    ).read_text(encoding="utf-8")


def test_phase557a_clean_windows_harness_is_no_auth_network_trust_first() -> None:
    script = _script()

    assert '[string]$Transport = "cloudflare"' in script
    assert '[string]$AcceptanceProfile = "5.5.7A"' in script
    assert '[string]$AllowedHost = "mcp.example.com"' in script
    assert "[int]$BridgePort = 46200" in script
    assert '$AcceptanceBridgeUrl = "http://127.0.0.1:$BridgePort/mcp"' in script
    assert '"--transport", "cloudflare"' in script
    assert '"--public-url", $PublicUrl' in script
    assert '"--origin-url", $OriginUrl' in script
    assert '"--metrics-addr", $MetricsAddr' in script
    assert '"--store-transport-secret"' in script
    assert '"--auth-mode", "none"' in script
    assert '"--network-trust", "cloudflare-chatgpt"' in script
    assert '"--allowed-host", $AllowedHost' in script
    assert '"--allowed-origin", $origin' in script


def test_phase557b_retains_external_oauth_resource_server_profile() -> None:
    script = _script()

    assert '"--auth-mode", "oauth-resource-server"' in script
    assert '"--authorization-server-issuer", $AuthorizationServerIssuer' in script
    assert '"--canonical-resource-uri", $CanonicalResourceUri' in script
    assert '"--validation-resource-id", $ValidationResourceId' in script
    assert '"--store-auth-secret"' in script


def test_phase557_prepare_reclaims_only_owned_project_registration() -> None:
    script = _script()

    assert "function Remove-AcceptanceProjectRegistration" in script
    assert '"project"' in script
    assert '"remove"' in script
    assert '"--expected-root"' in script
    assert '$result.status -ne "ok" -and $result.status -ne "not-found"' in script
    assert "$registration = Remove-AcceptanceProjectRegistration" in script
    assert "Prepare only manages the fixed Phase 5 project_id 'phase5-clean'" in script
    assert "Prepare only manages the fixed Phase 5 project root" in script


def test_phase557_prepare_classifies_existing_installation_before_upgrade() -> None:
    script = _script()

    assert "function Get-ExistingManagedAcceptanceInstall" in script
    assert "managed = $false" in script
    assert "function Read-Phase5ValidationState" in script
    assert "validation state is missing" in script
    assert "validation state is unreadable" in script
    assert "refusing to overwrite an unknown installation" in script
    assert "without an uninstall registration" in script
    assert "use a fresh Windows host/VM" not in script
    assert 'phase = "5.5.7"' in script
    assert "project_id = $ProjectId" in script
    assert "project_root = $projectRootPath" in script
    assert "app_root = $appRoot" in script


def test_phase557_managed_install_state_matches_fixed_acceptance_identity() -> None:
    script = _script()

    assert "function Assert-ManagedAcceptanceState" in script
    assert "ExpectedInstallDir" in script
    assert "ExpectedAppRoot" in script
    assert "ExpectedProjectRoot" in script
    assert "ExpectedProjectId" in script
    assert "ExpectedTransport" in script
    assert "ExpectedPublicUrl" in script
    assert "ExpectedBridgeUrl" in script
    assert "bridge_url" in script
    assert "ExpectedAuthorizationServerIssuer" in script
    assert "ExpectedCanonicalResourceUri" in script
    assert "ExpectedValidationResourceId" in script
    assert "current_installer_sha256" in script
    assert "previous_installer_sha256" in script
    assert "installed_executable_sha256" in script


def test_phase557_managed_upgrade_stops_owned_runtime_before_inno_setup() -> None:
    script = _script()

    stop_index = script.index("Stop-ManagedAcceptanceRuntime -ExistingInstall $existingAcceptance")
    setup_index = script.index("$setupExit = Invoke-GuiProcessAndWait -FilePath $installer")
    assert stop_index < setup_index
    assert '$stopArguments = @("stop") + $runtimeArguments' in script
    assert '"--app-root", $AcceptanceAppRoot' in script
    assert '"--home", $recordedHome' in script
    assert "managed Phase 5.5.7 runtime did not stop cleanly" in script
    assert "runtime ownership could not be proven stopped" in script
    assert '"/NOSTOPLIFECYCLE"' in script


def test_phase557_installed_payload_identity_is_verified_after_reinstall() -> None:
    script = _script()

    assert "function Get-InstalledExecutableIdentity" in script
    assert 'Join-Path $release.install_dir "SHA256SUMS.txt"' in script
    assert "Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256" in script
    assert "installed codemcp-remote.exe does not match its packaged checksum manifest" in script
    assert "left the previous executable artifact in place" in script
    assert 'Join-Path $release.install_dir "cloudflared.exe"' in script
    assert "$installedExecutableSha256 = Get-InstalledExecutableIdentity" in script


def test_phase557_prepare_records_current_and_previous_installer_identity() -> None:
    script = _script()

    state_index = script.index("$phase5State = [ordered]@{")
    current_index = script.index("current_installer_sha256 = $actualInstallerSha256", state_index)
    previous_index = script.index(
        "previous_installer_sha256 = $existingAcceptance.previous_installer_sha256", state_index
    )
    assert previous_index < current_index
    assert "installer_sha256 = $actualInstallerSha256" in script[state_index:]
    assert "installed_executable_sha256 = $installedExecutableSha256" in script[state_index:]
    assert "current_installer_sha256 = $actualInstallerSha256" in script


def test_phase557_prepare_rebuilds_project_and_records_fresh_baseline() -> None:
    script = _script()

    registration_index = script.index("$registration = Remove-AcceptanceProjectRegistration")
    baseline_index = script.index("$baselineHead = Prepare-AcceptanceProject")
    project_add_index = script.index('"project", "add", $ProjectId, $projectRootPath')
    state_index = script.index("baseline_head = $baselineHead")

    assert registration_index < baseline_index < project_add_index < state_index
    assert "Remove-Phase5AcceptanceTree -Path $projectRootPath" in script
    assert "baseline_head = $baselineHead" in script
    assert "--initial-branch=develop" in script
    assert "--initial-branch=main" not in script


def test_phase557_secrets_are_environment_only_and_rechecked_from_dpapi() -> None:
    script = _script()
    oauth_secret_guard = (
        'AcceptanceProfile -eq "5.5.7B" -and '
        "[string]::IsNullOrWhiteSpace($env:CODEMCP_RS_VERIFICATION_SECRET)"
    )

    assert "$env:TUNNEL_TOKEN" in script
    assert "$env:CODEMCP_RS_VERIFICATION_SECRET" in script
    assert "never pass the secret on the command line" in script
    assert "-TunnelToken" not in script
    assert "-VerificationSecret" not in script
    assert "$env:TUNNEL_TOKEN = $null" in script
    assert "$env:CODEMCP_RS_VERIFICATION_SECRET = $null" in script
    assert '$Doctor.checks.tunnel_token.source -ne "windows-dpapi"' in script
    assert '$Doctor.checks.auth.secret_source -ne "windows-dpapi"' in script
    assert oauth_secret_guard in script


def test_phase557a_does_not_require_oauth_verification_secret() -> None:
    script = _script()
    oauth_secret_guard = (
        'AcceptanceProfile -eq "5.5.7B" -and '
        "[string]::IsNullOrWhiteSpace($env:CODEMCP_RS_VERIFICATION_SECRET)"
    )

    assert oauth_secret_guard in script
    assert "Authentication=No authentication" in script
    assert '"--home", $AcceptanceHome' in script


def test_phase557_profiles_use_explicit_runtime_home() -> None:
    script = _script()

    assert '$AcceptanceHome = Join-Path $env:LOCALAPPDATA "codemcp-remote"' in script
    assert "function Get-AcceptanceRuntimeArguments" in script
    assert 'return @("--home", $AcceptanceHome)' in script
    assert "home = $AcceptanceHome" in script


def test_phase557_start_reuses_prepare_acceptance_identity() -> None:
    script = _script()
    start_index = script.index("function Invoke-Start")
    start_body = script[start_index : script.index("function Invoke-Cleanup", start_index)]

    assert "$preparedState = Read-Phase5ValidationState" in start_body
    assert 'Get-StateField -State $preparedState -Name "acceptance_profile"' in start_body
    assert 'Get-StateField -State $preparedState -Name "allowed_host"' in start_body
    assert 'Get-StateField -State $preparedState -Name "bridge_url"' in start_body
    assert "$AcceptanceProfile = $preparedProfile" in start_body
    assert "$AllowedHost = $preparedAllowedHost" in start_body
    assert "$AcceptanceBridgeUrl = $preparedBridgeUrl" in start_body
    assert "-ExpectedAcceptanceProfile $AcceptanceProfile" in start_body
    assert "-ExpectedAllowedHost $AllowedHost" in start_body
    assert "-ExpectedBridgeUrl $AcceptanceBridgeUrl" in start_body


def test_phase557_local_contract_requires_cloudflared_loopback_and_external_auth() -> None:
    script = _script()

    assert 'Join-Path $release.install_dir "cloudflared.exe"' in script
    assert "$Doctor.checks.configuration.bridge_url -ne $ExpectedBridgeUrl" in script
    assert "$Doctor.checks.cloudflare_settings.origin_url -ne $ExpectedBridgeUrl" in script
    assert '"mcp-rs-verification-v1"' in script
    assert "Assert-NoEmbeddedAuthServerState" in script
    assert '"*mcp-auth-server*"' in script
    assert "OAuth canonical resource does not match the Cloudflare public MCP URL" in script
    assert 'phase = "5.5.7"' in script


def test_phase557_disposable_project_has_profile_marker_but_no_codemcp_config() -> None:
    script = _script()

    assert '"README.md"' in script
    assert '"pyproject.toml"' in script
    assert '"PHASE5_ACCEPTANCE.txt"' in script
    assert 'name = "codemcp-remote-phase5-acceptance"' in script
    assert "add README.md pyproject.toml PHASE5_ACCEPTANCE.txt" in script
    assert 'Join-Path $Root "codemcp.toml"' not in script


def test_phase557_retains_explicit_openai_transport_compatibility() -> None:
    script = _script()

    assert '[ValidateSet("cloudflare", "openai-tunnel")]' in script
    assert '$Transport -eq "cloudflare"' in script
    assert '"--transport", "openai-tunnel"' in script
    assert '"--tunnel-id", $TunnelId' in script
    assert '"--bridge-url", $AcceptanceBridgeUrl' in script
    assert '"--store-api-key"' in script


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser check runs on native Windows")
def test_phase557_clean_windows_harness_parses_as_powershell() -> None:
    powershell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell 7 is unavailable")

    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "validate-clean-windows-release.ps1"
    )
    escaped_script_path = str(script_path).replace("'", "''")
    parser_command = (
        f"$path='{escaped_script_path}'; "
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
