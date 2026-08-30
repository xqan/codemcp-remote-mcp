[CmdletBinding()]
param(
    [ValidateSet("Prepare", "Start", "Cleanup", "Reset")]
    [string]$Action = "Prepare",
    [string]$InstallerPath,
    [string]$ExpectedInstallerSha256,
    [ValidateSet("cloudflare", "openai-tunnel")]
    [string]$Transport = "cloudflare",
    [ValidateSet("5.5.7A", "5.5.7B")]
    [string]$AcceptanceProfile = "5.5.7A",
    [string]$PublicUrl,
    [string]$AllowedHost = "mcp.example.com",
    [string[]]$AllowedOrigin,
    [string]$AuthorizationServerIssuer,
    [string]$CanonicalResourceUri,
    [string]$ValidationResourceId = "codemcp-resource",
    [ValidateRange(1, 65535)]
    [int]$BridgePort = 46200,
    [string]$OriginUrl,
    [string]$MetricsAddr = "127.0.0.1:46202",
    [string]$TunnelId,
    [string]$ProjectId = "phase5-clean",
    [string]$ProjectRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A26B4BA3-1D96-4F1A-95C4-9984C941A1E1}_is1"
$DefaultInstallDir = Join-Path $env:LOCALAPPDATA "Programs\codemcp-remote"
$AcceptanceHome = Join-Path $env:LOCALAPPDATA "codemcp-remote"
$AcceptanceAppRoot = $AcceptanceHome
$DefaultProjectRoot = Join-Path $env:LOCALAPPDATA "codemcp-remote-phase5\project"
$Phase5StateFile = Join-Path $AcceptanceHome "phase5-validation.json"
$AcceptanceBridgeUrl = "http://127.0.0.1:$BridgePort/mcp"
if ([string]::IsNullOrWhiteSpace($OriginUrl)) {
    $OriginUrl = $AcceptanceBridgeUrl
} elseif (-not [string]::Equals(
    $OriginUrl,
    $AcceptanceBridgeUrl,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "-OriginUrl must match the selected -BridgePort loopback MCP endpoint: $AcceptanceBridgeUrl"
}

function Invoke-GuiProcessAndWait {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru
    return $process.ExitCode
}

function Invoke-JsonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $output = (& $FilePath @ArgumentList 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$FilePath $($ArgumentList -join ' ') failed with exit code $exitCode`n$output"
    }
    try {
        return $output | ConvertFrom-Json
    } catch {
        throw "Command did not return valid JSON: $FilePath $($ArgumentList -join ' ')`n$output"
    }
}

function Get-AcceptanceRuntimeArguments {
    return @("--home", $AcceptanceHome)
}

function Remove-AcceptanceProjectRegistration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string]$ProjectId,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedRoot
    )

    $result = Invoke-JsonCommand -FilePath $FilePath -ArgumentList @(
        "project",
        "remove",
        $ProjectId,
        "--home",
        $AcceptanceHome,
        "--expected-root",
        $ExpectedRoot
    )
    if ($result.status -ne "ok" -and $result.status -ne "not-found") {
        throw "Phase 5 project registration removal did not complete safely"
    }
    return $result
}

function Require-CleanAcceptanceHost {
    if ($env:OS -ne "Windows_NT") {
        throw "Phase 5 clean-machine validation must run on Windows"
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Phase 5 currently supports only x64-compatible Windows"
    }
}

function Normalize-ComparablePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath.Length -gt 3) {
        $fullPath = $fullPath.TrimEnd("\")
    }
    return $fullPath
}

function Get-StateField {
    param(
        [Parameter(Mandatory = $true)]
        $State,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $property = $State.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Read-Phase5ValidationState {
    if (-not (Test-Path -LiteralPath $Phase5StateFile -PathType Leaf)) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install; validation state is missing"
    }
    try {
        $state = Get-Content -LiteralPath $Phase5StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install; validation state is unreadable"
    }
    if ($null -eq $state -or $state -is [System.Array]) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install; validation state is invalid"
    }
    return $state
}

function Assert-ManagedAcceptanceState {
    param(
        [Parameter(Mandatory = $true)]
        $State,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedInstallDir,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedAppRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedProjectId,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedTransport,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedAcceptanceProfile,
        [string]$ExpectedAllowedHost,
        [string]$ExpectedPublicUrl,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedBridgeUrl,
        [string]$ExpectedAuthorizationServerIssuer,
        [string]$ExpectedCanonicalResourceUri,
        [string]$ExpectedValidationResourceId
    )

    if ([string](Get-StateField -State $State -Name "phase") -ne "5.5.7") {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    $stateProfile = [string](Get-StateField -State $State -Name "acceptance_profile")
    if ([string]::IsNullOrWhiteSpace($stateProfile)) {
        # State written by the previous OAuth-first harness is the preserved
        # 5.5.7B profile, not an ambiguous No-Auth installation.
        $stateProfile = "5.5.7B"
    }
    if ($stateProfile -ne $ExpectedAcceptanceProfile) {
        throw "existing codemcp-remote acceptance profile does not match the requested profile"
    }
    if ([string](Get-StateField -State $State -Name "project_id") -ne $ExpectedProjectId) {
        throw "existing codemcp-remote installation is not owned by the fixed Phase 5 project"
    }

    $stateProjectRoot = [string](Get-StateField -State $State -Name "project_root")
    $stateInstallDir = [string](Get-StateField -State $State -Name "install_dir")
    if ([string]::IsNullOrWhiteSpace($stateProjectRoot) -or [string]::IsNullOrWhiteSpace($stateInstallDir)) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    try {
        $projectRootMatches = [string]::Equals(
            (Normalize-ComparablePath -Path $stateProjectRoot),
            (Normalize-ComparablePath -Path $ExpectedProjectRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
        $installDirMatches = [string]::Equals(
            (Normalize-ComparablePath -Path $stateInstallDir),
            (Normalize-ComparablePath -Path $ExpectedInstallDir),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    if (-not $projectRootMatches -or -not $installDirMatches) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }

    $stateAppRoot = [string](Get-StateField -State $State -Name "app_root")
    if ([string]::IsNullOrWhiteSpace($stateAppRoot)) {
        # State written before the managed-reinstall schema used this fixed state-file location.
        $stateAppRoot = $ExpectedAppRoot
    }
    try {
        $appRootMatches = [string]::Equals(
            (Normalize-ComparablePath -Path $stateAppRoot),
            (Normalize-ComparablePath -Path $ExpectedAppRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    if (-not $appRootMatches) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }

    $stateBridgeUrl = [string](Get-StateField -State $State -Name "bridge_url")
    if ([string]::IsNullOrWhiteSpace($stateBridgeUrl)) {
        # Older managed acceptance state predates bridge_url recording and used the fixed default.
        $stateBridgeUrl = "http://127.0.0.1:46200/mcp"
    }
    if (-not [string]::Equals(
        $stateBridgeUrl,
        $ExpectedBridgeUrl,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "existing codemcp-remote installation Bridge endpoint does not match the acceptance configuration"
    }

    if ([string](Get-StateField -State $State -Name "transport") -ne $ExpectedTransport) {
        throw "existing codemcp-remote installation transport does not match the acceptance configuration"
    }
    if ($ExpectedTransport -eq "cloudflare") {
        if ($ExpectedAcceptanceProfile -eq "5.5.7A") {
            $expectedFields = [ordered]@{
                auth_mode = "none"
                network_trust_mode = "cloudflare-chatgpt"
                allowed_host = $ExpectedAllowedHost
                identity_level = "network-only"
            }
        } else {
            $expectedFields = [ordered]@{
                public_url = $ExpectedPublicUrl
                auth_issuer = $ExpectedAuthorizationServerIssuer
                canonical_resource_uri = $ExpectedCanonicalResourceUri
                validation_resource_id = $ExpectedValidationResourceId
            }
        }
        foreach ($field in $expectedFields.Keys) {
            if ([string](Get-StateField -State $State -Name $field) -ne [string]$expectedFields[$field]) {
                throw "existing codemcp-remote installation security configuration does not match the acceptance configuration"
            }
        }
    }

    $legacyInstallerSha256 = [string](Get-StateField -State $State -Name "installer_sha256")
    $currentInstallerSha256 = [string](Get-StateField -State $State -Name "current_installer_sha256")
    if ([string]::IsNullOrWhiteSpace($currentInstallerSha256)) {
        $currentInstallerSha256 = $legacyInstallerSha256
    }
    if ($currentInstallerSha256 -notmatch "^[0-9a-fA-F]{64}$") {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    if (
        -not [string]::IsNullOrWhiteSpace($legacyInstallerSha256) -and
        $legacyInstallerSha256 -notmatch "^[0-9a-fA-F]{64}$"
    ) {
        throw "existing codemcp-remote installation is not a managed Phase 5.5.7 acceptance install"
    }
    if (
        -not [string]::IsNullOrWhiteSpace($legacyInstallerSha256) -and
        -not [string]::Equals(
            $legacyInstallerSha256,
            $currentInstallerSha256,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "existing codemcp-remote installation has inconsistent installer identity state"
    }

    $previousInstallerSha256 = [string](Get-StateField -State $State -Name "previous_installer_sha256")
    if (
        -not [string]::IsNullOrWhiteSpace($previousInstallerSha256) -and
        $previousInstallerSha256 -notmatch "^[0-9a-fA-F]{64}$"
    ) {
        throw "existing codemcp-remote installation has invalid installer identity state"
    }
    $recordedExecutableSha256 = [string](Get-StateField -State $State -Name "installed_executable_sha256")
    if (
        -not [string]::IsNullOrWhiteSpace($recordedExecutableSha256) -and
        $recordedExecutableSha256 -notmatch "^[0-9a-fA-F]{64}$"
    ) {
        throw "existing codemcp-remote installation has invalid executable identity state"
    }
}

function Get-InstalledExecutableIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath
    )

    $checksumManifest = Join-Path $InstallDir "SHA256SUMS.txt"
    if (-not (Test-Path -LiteralPath $checksumManifest -PathType Leaf)) {
        throw "installed release is missing its executable checksum manifest"
    }
    $matchingLines = @(
        Get-Content -LiteralPath $checksumManifest -Encoding ASCII |
            Where-Object { $_ -match "^([0-9A-Fa-f]{64})\s+codemcp-remote\.exe$" }
    )
    if ($matchingLines.Count -ne 1) {
        throw "installed release executable checksum manifest is invalid"
    }
    $manifestSha256 = ([regex]::Match($matchingLines[0], "^([0-9A-Fa-f]{64})\s+codemcp-remote\.exe$")).Groups[1].Value.ToLowerInvariant()
    $actualSha256 = (Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $manifestSha256) {
        throw "installed codemcp-remote.exe does not match its packaged checksum manifest"
    }
    return $actualSha256
}

function Get-ExistingManagedAcceptanceInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExpectedInstallDir,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedAppRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedProjectId,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedTransport,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedAcceptanceProfile,
        [string]$ExpectedAllowedHost,
        [string]$ExpectedPublicUrl,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedBridgeUrl,
        [string]$ExpectedAuthorizationServerIssuer,
        [string]$ExpectedCanonicalResourceUri,
        [string]$ExpectedValidationResourceId
    )

    if (-not (Test-Path -LiteralPath $UninstallKey)) {
        if (Test-Path -LiteralPath $ExpectedInstallDir) {
            throw "codemcp-remote files exist at the fixed install location without an uninstall registration; refusing to overwrite an unknown installation"
        }
        return [ordered]@{
            managed = $false
            release = $null
            state = $null
            previous_installer_sha256 = $null
            previous_installed_executable_sha256 = $null
        }
    }

    $release = Get-InstalledRelease
    if (
        -not [string]::Equals(
            (Normalize-ComparablePath -Path $release.install_dir),
            (Normalize-ComparablePath -Path $ExpectedInstallDir),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "codemcp-remote is installed outside the fixed Phase 5 acceptance location; refusing to overwrite an unknown installation"
    }

    $state = Read-Phase5ValidationState
    Assert-ManagedAcceptanceState `
        -State $state `
        -ExpectedInstallDir $ExpectedInstallDir `
        -ExpectedAppRoot $ExpectedAppRoot `
        -ExpectedProjectRoot $ExpectedProjectRoot `
        -ExpectedProjectId $ExpectedProjectId `
        -ExpectedTransport $ExpectedTransport `
        -ExpectedAcceptanceProfile $ExpectedAcceptanceProfile `
        -ExpectedAllowedHost $ExpectedAllowedHost `
        -ExpectedPublicUrl $ExpectedPublicUrl `
        -ExpectedBridgeUrl $ExpectedBridgeUrl `
        -ExpectedAuthorizationServerIssuer $ExpectedAuthorizationServerIssuer `
        -ExpectedCanonicalResourceUri $ExpectedCanonicalResourceUri `
        -ExpectedValidationResourceId $ExpectedValidationResourceId

    $installedExecutableSha256 = Get-InstalledExecutableIdentity `
        -InstallDir $release.install_dir `
        -ExecutablePath $release.exe
    $recordedExecutableSha256 = [string](Get-StateField -State $state -Name "installed_executable_sha256")
    if (
        -not [string]::IsNullOrWhiteSpace($recordedExecutableSha256) -and
        -not [string]::Equals(
            $recordedExecutableSha256,
            $installedExecutableSha256,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "existing codemcp-remote installation executable identity does not match validation state"
    }

    $currentInstallerSha256 = [string](Get-StateField -State $state -Name "current_installer_sha256")
    if ([string]::IsNullOrWhiteSpace($currentInstallerSha256)) {
        $currentInstallerSha256 = [string](Get-StateField -State $state -Name "installer_sha256")
    }
    return [ordered]@{
        managed = $true
        release = $release
        state = $state
        previous_installer_sha256 = $currentInstallerSha256.ToLowerInvariant()
        previous_installed_executable_sha256 = $installedExecutableSha256
    }
}

function Stop-ManagedAcceptanceRuntime {
    param(
        [Parameter(Mandatory = $true)]
        $ExistingInstall
    )

    $runtimeArguments = @("--app-root", $AcceptanceAppRoot)
    $recordedHome = [string](Get-StateField -State $ExistingInstall.state -Name "home")
    if (-not [string]::IsNullOrWhiteSpace($recordedHome)) {
        $runtimeArguments = @("--home", $recordedHome)
    }
    $stopArguments = @("stop") + $runtimeArguments
    $stop = Invoke-JsonCommand -FilePath $ExistingInstall.release.exe -ArgumentList $stopArguments
    if ($stop.status -ne "ok") {
        throw "managed Phase 5.5.7 runtime did not stop cleanly; refusing to overwrite the installation"
    }
    $notOwnedActions = @(
        $stop.actions | Where-Object { $_.status -eq "not_owned" }
    )
    if ($notOwnedActions.Count -gt 0) {
        throw "managed Phase 5.5.7 runtime ownership could not be proven stopped; refusing to overwrite the installation"
    }
}

function Resolve-Git {
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        throw "Git for Windows is required by the v0.1.0 release contract but was not found"
    }
    return $git.Source
}

function Get-InstalledRelease {
    if (-not (Test-Path -LiteralPath $UninstallKey)) {
        throw "codemcp-remote is not installed; run Action=Prepare first"
    }
    $registration = Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction Stop
    $registeredInstallLocation = [string]$registration.InstallLocation
    if ([string]::IsNullOrWhiteSpace($registeredInstallLocation)) {
        throw "installed codemcp-remote registration has no install location"
    }
    $installDir = [System.IO.Path]::GetFullPath($registeredInstallLocation)
    $exe = Join-Path $installDir "codemcp-remote.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "installed codemcp-remote.exe is missing: $exe"
    }
    return [ordered]@{
        install_dir = $installDir
        exe = $exe
    }
}

function Set-RuntimeIsolationPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallDir,
        [Parameter(Mandatory = $true)]
        [string]$GitPath
    )

    $entries = @(
        $InstallDir,
        (Split-Path -Parent $GitPath),
        (Join-Path $env:SystemRoot "System32")
    )
    $unique = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $entries) {
        if (-not [string]::IsNullOrWhiteSpace($entry) -and -not $unique.Contains($entry)) {
            $unique.Add($entry)
        }
    }
    $env:PATH = $unique -join ";"

    foreach ($forbidden in @("python.exe", "py.exe", "uv.exe", "pwsh.exe")) {
        if ($null -ne (Get-Command $forbidden -ErrorAction SilentlyContinue)) {
            throw "runtime isolation failed: $forbidden is still visible on PATH"
        }
    }
}

function Prepare-AcceptanceProject {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitPath,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    if (Test-Path -LiteralPath $Root) {
        throw "Phase 5 disposable project already exists: $Root"
    }
    New-Item -ItemType Directory -Force -Path $Root | Out-Null

    & $GitPath -C $Root init -q --initial-branch=develop
    if ($LASTEXITCODE -ne 0) { throw "git init failed" }
    & $GitPath -C $Root config user.name "codemcp-remote Phase 5"
    if ($LASTEXITCODE -ne 0) { throw "git user.name configuration failed" }
    & $GitPath -C $Root config user.email "phase5@localhost.invalid"
    if ($LASTEXITCODE -ne 0) { throw "git user.email configuration failed" }

    @"
# codemcp-remote Phase 5 clean-machine acceptance

This disposable repository exists only to validate the packaged Windows release.
"@ | Set-Content -LiteralPath (Join-Path $Root "README.md") -Encoding UTF8

    @"
[project]
name = "codemcp-remote-phase5-acceptance"
version = "0.0.0"
"@ | Set-Content -LiteralPath (Join-Path $Root "pyproject.toml") -Encoding ASCII

    "phase5-clean-machine" | Set-Content -LiteralPath (Join-Path $Root "PHASE5_ACCEPTANCE.txt") -Encoding ASCII

    & $GitPath -C $Root add README.md pyproject.toml PHASE5_ACCEPTANCE.txt
    if ($LASTEXITCODE -ne 0) { throw "git add failed" }
    & $GitPath -C $Root commit -q -m "chore: create phase5 acceptance baseline"
    if ($LASTEXITCODE -ne 0) { throw "git baseline commit failed" }

    $head = (& $GitPath -C $Root rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -notmatch "^[0-9a-f]{40,64}$") {
        throw "git rev-parse HEAD failed"
    }
    return $head
}

function Assert-DoctorContract {
    param(
        [Parameter(Mandatory = $true)]
        $Doctor,
        [ValidateSet("5.5.7A", "5.5.7B")]
        [string]$ExpectedAcceptanceProfile = "5.5.7A",
        [string]$ExpectedAllowedHost = "mcp.example.com",
        [Parameter(Mandatory = $true)]
        [string]$ExpectedBridgeUrl
    )

    if ($Doctor.status -ne "ok") {
        throw "doctor did not report status=ok"
    }
    if ($Doctor.checks.configuration.worker_mode -ne "local") {
        throw "clean-machine release is not using the native local worker"
    }
    if ([string]$Doctor.checks.configuration.bridge_url -ne $ExpectedBridgeUrl) {
        throw "doctor did not report the selected loopback Bridge MCP endpoint"
    }
    if ($Doctor.checks.git.status -ne "ok") {
        throw "doctor cannot find Git after runtime PATH isolation"
    }

    $provider = [string]$Doctor.checks.transport.provider
    if ($provider -eq "cloudflare") {
        if ($Doctor.checks.cloudflare_settings.status -ne "ok") {
            throw "doctor did not validate Cloudflare transport settings"
        }
        if ([string]$Doctor.checks.cloudflare_settings.origin_url -ne $ExpectedBridgeUrl) {
            throw "Cloudflare origin does not match the selected loopback Bridge MCP endpoint"
        }
        if ($Doctor.checks.cloudflared.status -ne "ok") {
            throw "doctor cannot find the bundled cloudflared"
        }
        if (
            $Doctor.checks.tunnel_token.status -ne "ok" -or
            $Doctor.checks.tunnel_token.source -ne "windows-dpapi"
        ) {
            throw "doctor did not prove Cloudflare tunnel-token DPAPI recovery"
        }
        if ($ExpectedAcceptanceProfile -eq "5.5.7A") {
            if (
                $Doctor.checks.auth.status -ne "ready" -or
                $Doctor.checks.auth.mode -ne "none" -or
                $Doctor.checks.auth.oauth_secret_required -ne $false
            ) {
                throw "doctor did not prove the 5.5.7A No-Auth profile"
            }
            if (
                $Doctor.checks.network_trust.status -ne "ready" -or
                $Doctor.checks.network_trust.mode -ne "cloudflare-chatgpt" -or
                $Doctor.checks.network_trust.allowed_hosts -notcontains $ExpectedAllowedHost
            ) {
                throw "doctor did not prove the Cloudflare ChatGPT network-trust profile"
            }
            if ($Doctor.checks.identity_level -ne "network-only") {
                throw "doctor did not report network-only identity semantics"
            }
            return
        }
        if (
            $Doctor.checks.auth.status -ne "ok" -or
            $Doctor.checks.auth.mode -ne "oauth-resource-server" -or
            $Doctor.checks.auth.verification_contract -ne "mcp-rs-verification-v1" -or
            $Doctor.checks.auth.secret_source -ne "windows-dpapi"
        ) {
            throw "doctor did not prove the external OAuth Resource Server contract and DPAPI secret recovery"
        }
        if ([string]$Doctor.checks.auth.resource -ne [string]$Doctor.checks.cloudflare_settings.public_url) {
            throw "OAuth canonical resource does not match the Cloudflare public MCP URL"
        }
        return
    }

    if ($provider -ne "openai-tunnel") {
        throw "doctor reported an unsupported transport provider: $provider"
    }
    if ($Doctor.checks.api_key.status -ne "ok" -or $Doctor.checks.api_key.source -ne "windows-dpapi") {
        throw "doctor did not prove Windows DPAPI secret recovery"
    }
    if ($Doctor.checks.tunnel_client.status -ne "ok") {
        throw "doctor cannot find the bundled tunnel-client"
    }
}

function Assert-NoEmbeddedAuthServerState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AppRoot
    )

    if (-not (Test-Path -LiteralPath $AppRoot -PathType Container)) {
        return
    }
    $forbiddenPatterns = @(
        "*mcp-auth-server*",
        "*signing-key*",
        "*private-key*",
        "*users*.sqlite*",
        "*clients*.sqlite*",
        "*refresh-token*"
    )
    foreach ($pattern in $forbiddenPatterns) {
        $found = Get-ChildItem -LiteralPath $AppRoot -Recurse -Force -ErrorAction Stop |
            Where-Object { $_.Name -like $pattern } |
            Select-Object -First 1
        if ($null -ne $found) {
            throw "codemcp-remote runtime unexpectedly contains auth-server private state: $($found.FullName)"
        }
    }
}

function Invoke-Start {
    $release = Get-InstalledRelease
    $gitPath = Resolve-Git
    Set-RuntimeIsolationPath -InstallDir $release.install_dir -GitPath $gitPath

    $env:CONTROL_PLANE_API_KEY = $null
    $env:TUNNEL_TOKEN = $null
    $env:CODEMCP_RS_VERIFICATION_SECRET = $null
    $runtimeArguments = Get-AcceptanceRuntimeArguments

    # Start must reuse the acceptance identity frozen by Prepare. Command-line
    # defaults are only defaults for Prepare and must not silently replace the
    # managed state when Start is invoked without repeating all Prepare args.
    $preparedState = Read-Phase5ValidationState
    $preparedProfile = [string](Get-StateField -State $preparedState -Name "acceptance_profile")
    if ([string]::IsNullOrWhiteSpace($preparedProfile)) {
        # Preserve the legacy managed-state interpretation used elsewhere in
        # this harness for state written before the explicit profile field.
        $preparedProfile = "5.5.7B"
    }
    if ($preparedProfile -notin @("5.5.7A", "5.5.7B")) {
        throw "managed acceptance state has an unsupported acceptance_profile"
    }
    $AcceptanceProfile = $preparedProfile

    $preparedBridgeUrl = [string](Get-StateField -State $preparedState -Name "bridge_url")
    if (-not [string]::IsNullOrWhiteSpace($preparedBridgeUrl)) {
        $AcceptanceBridgeUrl = $preparedBridgeUrl
    }
    if ($AcceptanceProfile -eq "5.5.7A") {
        $preparedAllowedHost = [string](Get-StateField -State $preparedState -Name "allowed_host")
        if ([string]::IsNullOrWhiteSpace($preparedAllowedHost)) {
            throw "managed 5.5.7A acceptance state is missing allowed_host"
        }
        $AllowedHost = $preparedAllowedHost
    }

    $doctor = Invoke-JsonCommand -FilePath $release.exe -ArgumentList (@("doctor") + $runtimeArguments)
    Assert-DoctorContract `
        -Doctor $doctor `
        -ExpectedAcceptanceProfile $AcceptanceProfile `
        -ExpectedAllowedHost $AllowedHost `
        -ExpectedBridgeUrl $AcceptanceBridgeUrl
    $appRoot = $AcceptanceAppRoot
    Assert-NoEmbeddedAuthServerState -AppRoot $appRoot

    $start = Invoke-JsonCommand `
        -FilePath $release.exe `
        -ArgumentList (@("start", "--startup-timeout", "45") + $runtimeArguments)
    if ($start.status -ne "ok") {
        throw "native lifecycle start failed"
    }
    $status = Invoke-JsonCommand -FilePath $release.exe -ArgumentList (@("status") + $runtimeArguments)
    if ($status.status -ne "running") {
        throw "native lifecycle did not reach running state"
    }
    if (-not $status.bridge.owned -or $status.bridge.health.status -ne "ok") {
        throw "Bridge is not healthy and owned"
    }
    if (-not $status.tunnel.owned -or $status.tunnel.health.status -ne "ok") {
        throw "Tunnel is not healthy and owned"
    }

    $phase5 = $null
    if (Test-Path -LiteralPath $Phase5StateFile -PathType Leaf) {
        try {
            $phase5 = Get-Content -LiteralPath $Phase5StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            throw "Phase 5 validation state is unreadable: $Phase5StateFile"
        }
    }

    $provider = [string]$doctor.checks.transport.provider
    $transportSecretSource = if ($provider -eq "cloudflare") {
        [string]$doctor.checks.tunnel_token.source
    } else {
        [string]$doctor.checks.api_key.source
    }
    $transportClientPath = if ($provider -eq "cloudflare") {
        [string]$doctor.checks.cloudflared.path
    } else {
        [string]$doctor.checks.tunnel_client.path
    }
    $phase5PreviousInstallerSha256 = if ($null -ne $phase5) {
        [string](Get-StateField -State $phase5 -Name "previous_installer_sha256")
    } else {
        $null
    }
    $phase5CurrentInstallerSha256 = if ($null -ne $phase5) {
        $current = [string](Get-StateField -State $phase5 -Name "current_installer_sha256")
        if ([string]::IsNullOrWhiteSpace($current)) {
            [string](Get-StateField -State $phase5 -Name "installer_sha256")
        } else {
            $current
        }
    } else {
        $null
    }
    $phase5InstalledExecutableSha256 = if ($null -ne $phase5) {
        [string](Get-StateField -State $phase5 -Name "installed_executable_sha256")
    } else {
        $null
    }

    [ordered]@{
        status = "ready-for-remote-verification"
        phase = "5.5.7"
        acceptance_profile = $AcceptanceProfile
        action = "start"
        install_dir = $release.install_dir
        home = $AcceptanceHome
        app_root = $AcceptanceAppRoot
        project_id = if ($null -ne $phase5) { [string](Get-StateField -State $phase5 -Name "project_id") } else { $null }
        project_root = if ($null -ne $phase5) { [string](Get-StateField -State $phase5 -Name "project_root") } else { $null }
        baseline_head = if ($null -ne $phase5) { [string](Get-StateField -State $phase5 -Name "baseline_head") } else { $null }
        previous_installer_sha256 = $phase5PreviousInstallerSha256
        current_installer_sha256 = $phase5CurrentInstallerSha256
        installed_executable_sha256 = $phase5InstalledExecutableSha256
        worker_mode = [string]$doctor.checks.configuration.worker_mode
        bridge_url = [string]$doctor.checks.configuration.bridge_url
        git_path = $gitPath
        transport = $provider
        transport_secret_source = $transportSecretSource
        transport_client_path = $transportClientPath
        public_url = if ($provider -eq "cloudflare") { [string]$doctor.checks.cloudflare_settings.public_url } else { $null }
        auth_mode = if ($provider -eq "cloudflare") { [string]$doctor.checks.auth.mode } else { $null }
        network_trust_mode = if ($provider -eq "cloudflare") { [string]$doctor.checks.network_trust.mode } else { $null }
        identity_level = if ($provider -eq "cloudflare") { [string]$doctor.checks.identity_level } else { $null }
        oauth_secret_required = if ($provider -eq "cloudflare") { [bool]$doctor.checks.auth.oauth_secret_required } else { $false }
        auth_issuer = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctor.checks.auth.issuer } else { $null }
        canonical_resource_uri = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctor.checks.auth.resource } else { $null }
        auth_secret_source = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctor.checks.auth.secret_source } else { $null }
        bridge_health = [string]$status.bridge.health.status
        tunnel_health = [string]$status.tunnel.health.status
        python_visible_on_isolated_path = ($null -ne (Get-Command python.exe -ErrorAction SilentlyContinue))
        uv_visible_on_isolated_path = ($null -ne (Get-Command uv.exe -ErrorAction SilentlyContinue))
        pwsh_visible_on_isolated_path = ($null -ne (Get-Command pwsh.exe -ErrorAction SilentlyContinue))
        wsl_command_visible = ($null -ne (Get-Command wsl.exe -ErrorAction SilentlyContinue))
        next = if ($AcceptanceProfile -eq "5.5.7A") {
            "Use the ChatGPT connector with Authentication=No authentication through the Cloudflare MCP URL and run the Phase 5.5.7A remote contract before cleanup."
        } else {
            "Use the ChatGPT connector through the authenticated Cloudflare MCP URL and run the Phase 5.5.7B remote contract before cleanup."
        }
    } | ConvertTo-Json -Depth 7
}

function Invoke-Cleanup {
    if (-not (Test-Path -LiteralPath $UninstallKey)) {
        [ordered]@{
            status = "ok"
            phase = "5"
            action = "cleanup"
            note = "codemcp-remote is not installed"
        } | ConvertTo-Json -Depth 5
        return
    }

    $release = Get-InstalledRelease
    # A state file from the pre-5.5.7A OAuth-first harness has no home field
    # and therefore uses the legacy --app-root layout until Prepare upgrades it.
    $cleanupRuntimeArguments = @("--app-root", $AcceptanceAppRoot)
    if (Test-Path -LiteralPath $Phase5StateFile -PathType Leaf) {
        try {
            $cleanupState = Get-Content -LiteralPath $Phase5StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $recordedHome = [string](Get-StateField -State $cleanupState -Name "home")
            if (-not [string]::IsNullOrWhiteSpace($recordedHome)) {
                $cleanupRuntimeArguments = @("--home", $recordedHome)
            }
        } catch {
            throw "Phase 5 validation state is unreadable: $Phase5StateFile"
        }
    }
    & $release.exe stop @cleanupRuntimeArguments | Out-Host

    $uninstallers = @(Get-ChildItem -LiteralPath $release.install_dir -File -Filter "unins*.exe" -ErrorAction SilentlyContinue)
    if ($uninstallers.Count -ne 1) {
        throw "expected exactly one Inno Setup uninstaller in $($release.install_dir)"
    }
    $exitCode = Invoke-GuiProcessAndWait -FilePath $uninstallers[0].FullName -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART"
    )
    if ($exitCode -ne 0) {
        throw "clean-machine uninstall failed with exit code $exitCode"
    }
    if (Test-Path -LiteralPath $UninstallKey) {
        throw "uninstall registration still exists after cleanup"
    }

    [ordered]@{
        status = "ok"
        phase = "5"
        action = "cleanup"
        install_dir = $release.install_dir
        note = "installer removed; runtime data and disposable project are intentionally preserved"
    } | ConvertTo-Json -Depth 5
}

function Remove-Phase5AcceptanceTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    $allowedPaths = @(
        [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "codemcp-remote")).TrimEnd("\"),
        [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "codemcp-remote-phase5")).TrimEnd("\"),
        [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "codemcp-remote-phase5\project")).TrimEnd("\")
    )
    if ($allowedPaths -notcontains $fullPath) {
        throw "Reset refused to remove a path outside the fixed Phase 5 acceptance roots: $fullPath"
    }

    $existingAncestor = $fullPath
    while (-not (Test-Path -LiteralPath $existingAncestor)) {
        $parent = Split-Path -Parent $existingAncestor
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $existingAncestor) {
            break
        }
        $existingAncestor = $parent
    }
    if (Test-Path -LiteralPath $existingAncestor) {
        $ancestorItem = Get-Item -LiteralPath $existingAncestor -Force
        if (($ancestorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reset refused to traverse a reparse-point acceptance ancestor: $existingAncestor"
        }
    }
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return
    }

    $item = Get-Item -LiteralPath $fullPath -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reset refused to remove a reparse-point acceptance root: $fullPath"
    }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}

function Invoke-Reset {
    if (Test-Path -LiteralPath $UninstallKey) {
        throw "Reset requires codemcp-remote to be uninstalled first; run Action=Cleanup"
    }

    $appRoot = $AcceptanceAppRoot
    $acceptanceRoot = Join-Path $env:LOCALAPPDATA "codemcp-remote-phase5"
    Remove-Phase5AcceptanceTree -Path $appRoot
    Remove-Phase5AcceptanceTree -Path $acceptanceRoot

    [ordered]@{
        status = "ok"
        phase = "5"
        action = "reset"
        removed = @($appRoot, $acceptanceRoot)
        note = "Phase 5 acceptance-only runtime and disposable project state removed"
    } | ConvertTo-Json -Depth 5
}

if ($Action -eq "Start") {
    Invoke-Start
    exit 0
}
if ($Action -eq "Cleanup") {
    Invoke-Cleanup
    exit 0
}
if ($Action -eq "Reset") {
    Invoke-Reset
    exit 0
}

Require-CleanAcceptanceHost

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    throw "-InstallerPath is required for Action=Prepare"
}
if ($ExpectedInstallerSha256 -notmatch "^[0-9a-fA-F]{64}$") {
    throw "-ExpectedInstallerSha256 must be a 64-character SHA-256 digest"
}
if ($Transport -eq "cloudflare") {
    if ([string]::IsNullOrWhiteSpace($PublicUrl)) {
        throw "-PublicUrl is required for Cloudflare Action=Prepare"
    }
    if ($AcceptanceProfile -eq "5.5.7A") {
        if ([string]::IsNullOrWhiteSpace($AllowedHost)) {
            throw "-AllowedHost is required for the 5.5.7A Cloudflare profile"
        }
        $AllowedHost = $AllowedHost.ToLowerInvariant()
    } else {
        if ([string]::IsNullOrWhiteSpace($AuthorizationServerIssuer)) {
            throw "-AuthorizationServerIssuer is required for the 5.5.7B Cloudflare profile"
        }
        if ([string]::IsNullOrWhiteSpace($CanonicalResourceUri)) {
            $CanonicalResourceUri = $PublicUrl
        }
    }
    if ([string]::IsNullOrWhiteSpace($env:TUNNEL_TOKEN)) {
        throw "Set TUNNEL_TOKEN in this process before Cloudflare Action=Prepare; never pass the secret on the command line"
    }
    if ($AcceptanceProfile -eq "5.5.7B" -and [string]::IsNullOrWhiteSpace($env:CODEMCP_RS_VERIFICATION_SECRET)) {
        throw "Set CODEMCP_RS_VERIFICATION_SECRET in this process before Cloudflare Action=Prepare; never pass the secret on the command line"
    }
} else {
    if ([string]::IsNullOrWhiteSpace($TunnelId)) {
        throw "-TunnelId is required for openai-tunnel Action=Prepare"
    }
    if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
        throw "Set CONTROL_PLANE_API_KEY in this process before openai-tunnel Action=Prepare; never pass the secret on the command line"
    }
}

$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$actualInstallerSha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualInstallerSha256 -ne $ExpectedInstallerSha256.ToLowerInvariant()) {
    throw "installer SHA-256 mismatch: expected=$ExpectedInstallerSha256 actual=$actualInstallerSha256"
}

$gitPath = Resolve-Git
$defaultProjectRootPath = [System.IO.Path]::GetFullPath($DefaultProjectRoot).TrimEnd("\")
if ($ProjectId -ne "phase5-clean") {
    throw "Prepare only manages the fixed Phase 5 project_id 'phase5-clean'"
}
$projectRootPath = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $DefaultProjectRoot
} else {
    [System.IO.Path]::GetFullPath($ProjectRoot)
}
$requestedProjectRootPath = [System.IO.Path]::GetFullPath($projectRootPath).TrimEnd("\")
if (-not [string]::Equals($requestedProjectRootPath, $defaultProjectRootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Prepare only manages the fixed Phase 5 project root: $DefaultProjectRoot"
}

$expectedInstallDir = Normalize-ComparablePath -Path $DefaultInstallDir
$expectedAppRoot = Normalize-ComparablePath -Path $AcceptanceAppRoot
$existingAcceptance = Get-ExistingManagedAcceptanceInstall `
    -ExpectedInstallDir $expectedInstallDir `
    -ExpectedAppRoot $expectedAppRoot `
    -ExpectedProjectRoot $projectRootPath `
    -ExpectedProjectId $ProjectId `
    -ExpectedTransport $Transport `
    -ExpectedAcceptanceProfile $AcceptanceProfile `
    -ExpectedAllowedHost $AllowedHost `
    -ExpectedPublicUrl $PublicUrl `
    -ExpectedBridgeUrl $AcceptanceBridgeUrl `
    -ExpectedAuthorizationServerIssuer $AuthorizationServerIssuer `
    -ExpectedCanonicalResourceUri $CanonicalResourceUri `
    -ExpectedValidationResourceId $ValidationResourceId

if ($existingAcceptance.managed) {
    Stop-ManagedAcceptanceRuntime -ExistingInstall $existingAcceptance
}

$installerArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    ('/DIR="{0}"' -f $expectedInstallDir),
    "/MERGETASKS=!addtopath"
)
if ($existingAcceptance.managed) {
    # The harness already performed the formal lifecycle stop and must not let
    # Inno Setup target any process outside that managed runtime boundary.
    $installerArguments += "/NOSTOPLIFECYCLE"
}
$setupExit = Invoke-GuiProcessAndWait -FilePath $installer -ArgumentList $installerArguments
if ($setupExit -ne 0) {
    throw "clean-machine installer failed with exit code $setupExit"
}
if (-not (Test-Path -LiteralPath $UninstallKey)) {
    throw "installer did not create the expected uninstall registration"
}

$release = Get-InstalledRelease
$releaseInstallDir = Normalize-ComparablePath -Path $release.install_dir
if (-not [string]::Equals($releaseInstallDir, $expectedInstallDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "unexpected default install location: $($release.install_dir)"
}

$tunnelExe = Join-Path $release.install_dir "tunnel-client.exe"
$cloudflaredExe = Join-Path $release.install_dir "cloudflared.exe"
foreach ($required in @(
    $release.exe,
    $cloudflaredExe,
    $tunnelExe,
    (Join-Path $release.install_dir "LICENSE"),
    (Join-Path $release.install_dir "THIRD_PARTY_NOTICES.txt"),
    (Join-Path $release.install_dir "SHA256SUMS.txt")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "installed release is missing required file: $required"
    }
}
foreach ($forbiddenBundledTool in @("python.exe", "uv.exe", "pwsh.exe", "wsl.exe")) {
    if (Test-Path -LiteralPath (Join-Path $release.install_dir $forbiddenBundledTool) -PathType Leaf) {
        throw "installer unexpectedly bundles $forbiddenBundledTool"
    }
}
$installedExecutableSha256 = Get-InstalledExecutableIdentity `
    -InstallDir $release.install_dir `
    -ExecutablePath $release.exe
if (
    $existingAcceptance.managed -and
    $actualInstallerSha256 -ne $existingAcceptance.previous_installer_sha256 -and
    $installedExecutableSha256 -eq $existingAcceptance.previous_installed_executable_sha256
) {
    throw "managed installer upgrade left the previous executable artifact in place; refusing to continue"
}

Set-RuntimeIsolationPath -InstallDir $release.install_dir -GitPath $gitPath

$versionText = (& $release.exe --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionText -notmatch "0\.1\.0") {
    throw "installed codemcp-remote version check failed: $versionText"
}

$initArgs = if ($Transport -eq "cloudflare") {
    if ($AcceptanceProfile -eq "5.5.7A") {
        $profileArgs = @(
            "init",
            "--home", $AcceptanceHome,
            "--transport", "cloudflare",
            "--public-url", $PublicUrl,
            "--origin-url", $OriginUrl,
            "--metrics-addr", $MetricsAddr,
            "--store-transport-secret",
            "--auth-mode", "none",
            "--network-trust", "cloudflare-chatgpt",
            "--allowed-host", $AllowedHost
        )
        foreach ($origin in @($AllowedOrigin)) {
            if (-not [string]::IsNullOrWhiteSpace($origin)) {
                $profileArgs += @("--allowed-origin", $origin)
            }
        }
        $profileArgs
    } else {
        @(
            "init",
            "--home", $AcceptanceHome,
            "--transport", "cloudflare",
            "--public-url", $PublicUrl,
            "--origin-url", $OriginUrl,
            "--metrics-addr", $MetricsAddr,
            "--store-transport-secret",
            "--auth-mode", "oauth-resource-server",
            "--authorization-server-issuer", $AuthorizationServerIssuer,
            "--canonical-resource-uri", $CanonicalResourceUri,
            "--validation-resource-id", $ValidationResourceId,
            "--store-auth-secret"
        )
    }
} else {
    @(
        "init",
        "--home", $AcceptanceHome,
        "--transport", "openai-tunnel",
        "--tunnel-id", $TunnelId,
        "--bridge-url", $AcceptanceBridgeUrl,
        "--store-api-key"
    )
}
$init = Invoke-JsonCommand -FilePath $release.exe -ArgumentList $initArgs
if ($init.status -ne "ok") {
    throw "codemcp-remote init did not report status=ok"
}

$env:CONTROL_PLANE_API_KEY = $null
$env:TUNNEL_TOKEN = $null
$env:CODEMCP_RS_VERIFICATION_SECRET = $null
$runtimeArguments = Get-AcceptanceRuntimeArguments
$doctor = Invoke-JsonCommand -FilePath $release.exe -ArgumentList (@("doctor") + $runtimeArguments)
Assert-DoctorContract `
    -Doctor $doctor `
    -ExpectedAcceptanceProfile $AcceptanceProfile `
    -ExpectedAllowedHost $AllowedHost `
    -ExpectedBridgeUrl $AcceptanceBridgeUrl

$registration = Remove-AcceptanceProjectRegistration `
    -FilePath $release.exe `
    -ProjectId $ProjectId `
    -ExpectedRoot $projectRootPath
Remove-Phase5AcceptanceTree -Path $projectRootPath

$baselineHead = Prepare-AcceptanceProject -GitPath $gitPath -Root $projectRootPath
$project = Invoke-JsonCommand -FilePath $release.exe -ArgumentList @(
    "project", "add", $ProjectId, $projectRootPath,
    "--home", $AcceptanceHome
)
if ($project.status -ne "ok") {
    throw "project registration failed"
}

$doctorAfterProject = Invoke-JsonCommand -FilePath $release.exe -ArgumentList (@("doctor") + $runtimeArguments)
Assert-DoctorContract `
    -Doctor $doctorAfterProject `
    -ExpectedAcceptanceProfile $AcceptanceProfile `
    -ExpectedAllowedHost $AllowedHost `
    -ExpectedBridgeUrl $AcceptanceBridgeUrl
if ([int]$doctorAfterProject.checks.configuration.projects -lt 1) {
    throw "doctor did not observe the registered Phase 5 project"
}
$appRoot = $AcceptanceAppRoot
Assert-NoEmbeddedAuthServerState -AppRoot $appRoot

$provider = [string]$doctorAfterProject.checks.transport.provider
$transportSecretSource = if ($provider -eq "cloudflare") {
    [string]$doctorAfterProject.checks.tunnel_token.source
} else {
    [string]$doctorAfterProject.checks.api_key.source
}
$transportClientPath = if ($provider -eq "cloudflare") {
    [string]$doctorAfterProject.checks.cloudflared.path
} else {
    [string]$doctorAfterProject.checks.tunnel_client.path
}

$phase5State = [ordered]@{
    phase = "5.5.7"
    acceptance_profile = $AcceptanceProfile
    project_id = $ProjectId
    project_root = $projectRootPath
    baseline_head = $baselineHead
    home = $AcceptanceHome
    app_root = $appRoot
    config_dir = [string]$doctorAfterProject.checks.paths.config
    projects_config = [string]$doctorAfterProject.checks.paths.projects_config
    data_dir = [string]$doctorAfterProject.checks.paths.data
    secrets_dir = [string]$doctorAfterProject.checks.paths.secrets
    installer_sha256 = $actualInstallerSha256
    previous_installer_sha256 = $existingAcceptance.previous_installer_sha256
    current_installer_sha256 = $actualInstallerSha256
    installed_executable_sha256 = $installedExecutableSha256
    previous_installed_executable_sha256 = $existingAcceptance.previous_installed_executable_sha256
    install_dir = $release.install_dir
    bridge_url = [string]$doctorAfterProject.checks.configuration.bridge_url
    transport = $provider
    public_url = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.cloudflare_settings.public_url } else { $null }
    auth_mode = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.auth.mode } else { $null }
    network_trust_mode = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.network_trust.mode } else { $null }
    allowed_host = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.network_trust.allowed_hosts[0] } else { $null }
    identity_level = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.identity_level } else { $null }
    oauth_secret_required = if ($provider -eq "cloudflare") { [bool]$doctorAfterProject.checks.auth.oauth_secret_required } else { $false }
    auth_issuer = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.issuer } else { $null }
    canonical_resource_uri = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.resource } else { $null }
    validation_resource_id = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.validation_resource_id } else { $null }
}
$stateParent = Split-Path -Parent $Phase5StateFile
New-Item -ItemType Directory -Force -Path $stateParent | Out-Null
$phase5State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Phase5StateFile -Encoding UTF8

[ordered]@{
    status = "ready-for-start"
    phase = "5.5.7"
    acceptance_profile = $AcceptanceProfile
    action = "prepare"
    installer_sha256 = $actualInstallerSha256
    previous_installer_sha256 = $existingAcceptance.previous_installer_sha256
    current_installer_sha256 = $actualInstallerSha256
    installed_executable_sha256 = $installedExecutableSha256
    install_dir = $release.install_dir
    home = $AcceptanceHome
    app_root = $appRoot
    config_dir = [string]$doctorAfterProject.checks.paths.config
    projects_config = [string]$doctorAfterProject.checks.paths.projects_config
    data_dir = [string]$doctorAfterProject.checks.paths.data
    secrets_dir = [string]$doctorAfterProject.checks.paths.secrets
    phase5_state_file = $Phase5StateFile
    project_id = $ProjectId
    project_root = $projectRootPath
    baseline_head = $baselineHead
    worker_mode = [string]$doctorAfterProject.checks.configuration.worker_mode
    bridge_url = [string]$doctorAfterProject.checks.configuration.bridge_url
    git_path = $gitPath
    transport = $provider
    transport_secret_source = $transportSecretSource
    transport_client_path = $transportClientPath
    auth_mode = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.auth.mode } else { $null }
    network_trust_mode = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.network_trust.mode } else { $null }
    identity_level = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.identity_level } else { $null }
    oauth_secret_required = if ($provider -eq "cloudflare") { [bool]$doctorAfterProject.checks.auth.oauth_secret_required } else { $false }
    public_url = if ($provider -eq "cloudflare") { [string]$doctorAfterProject.checks.cloudflare_settings.public_url } else { $null }
    auth_issuer = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.issuer } else { $null }
    canonical_resource_uri = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.resource } else { $null }
    auth_secret_source = if ($provider -eq "cloudflare" -and $AcceptanceProfile -eq "5.5.7B") { [string]$doctorAfterProject.checks.auth.secret_source } else { $null }
    python_visible_on_isolated_path = ($null -ne (Get-Command python.exe -ErrorAction SilentlyContinue))
    uv_visible_on_isolated_path = ($null -ne (Get-Command uv.exe -ErrorAction SilentlyContinue))
    pwsh_visible_on_isolated_path = ($null -ne (Get-Command pwsh.exe -ErrorAction SilentlyContinue))
    wsl_command_visible = ($null -ne (Get-Command wsl.exe -ErrorAction SilentlyContinue))
    next = if ($AcceptanceProfile -eq "5.5.7A") {
        "Run Action=Start, then use ChatGPT Connector Authentication=No authentication for the Phase 5.5.7A remote contract."
    } else {
        "Run Action=Start, then connect ChatGPT to the OAuth Cloudflare MCP URL for the Phase 5.5.7B remote contract."
    }
} | ConvertTo-Json -Depth 7
