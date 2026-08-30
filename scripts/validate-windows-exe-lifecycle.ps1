[CmdletBinding()]
param(
    [string]$Executable,
    [string]$BridgeConfig,
    [string]$ProjectsConfig,
    [string]$EnvFile,
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSec = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $repositoryRoot ".local\dist\codemcp-remote\codemcp-remote.exe"
}
if ([string]::IsNullOrWhiteSpace($BridgeConfig)) {
    $BridgeConfig = Join-Path $repositoryRoot "config\bridge.example.toml"
}
if ([string]::IsNullOrWhiteSpace($ProjectsConfig)) {
    $ProjectsConfig = Join-Path $repositoryRoot "config\projects.toml"
    if (-not (Test-Path -LiteralPath $ProjectsConfig -PathType Leaf)) {
        $ProjectsConfig = Join-Path $repositoryRoot "config\projects.example.toml"
    }
}
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $repositoryRoot "config\tunnel-profile.local.env"
}

$exePath = (Resolve-Path -LiteralPath $Executable).Path
$bridgeConfigPath = (Resolve-Path -LiteralPath $BridgeConfig).Path
$projectsConfigPath = (Resolve-Path -LiteralPath $ProjectsConfig).Path
$envFilePath = (Resolve-Path -LiteralPath $EnvFile).Path
$legacyStop = Join-Path $repositoryRoot "scripts\stop-all.ps1"
$legacyStart = Join-Path $repositoryRoot "scripts\start-all.ps1"

function Invoke-NativeLifecycle {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $exePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "codemcp-remote.exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-NativeLifecycleStatus {
    $raw = (& $exePath status 2>&1 | Out-String).Trim()
    try {
        return $raw | ConvertFrom-Json
    } catch {
        throw "codemcp-remote.exe status did not return valid JSON: $raw"
    }
}

Write-Host "Phase 3 preflight: frozen doctor"
Invoke-NativeLifecycle -Arguments @(
    "doctor",
    "--bridge-config", $bridgeConfigPath,
    "--projects-config", $projectsConfigPath,
    "--env-file", $envFilePath
)

$currentStatus = Get-NativeLifecycleStatus
$hasBridge = $currentStatus.PSObject.Properties.Name -contains "bridge"
$hasTunnel = $currentStatus.PSObject.Properties.Name -contains "tunnel"
$bridgeOwned = $hasBridge -and ($currentStatus.bridge.owned -eq $true)
$tunnelOwned = $hasTunnel -and ($currentStatus.tunnel.owned -eq $true)
$nativeOwned = $bridgeOwned -or $tunnelOwned
$nativeHealthy = (
    $currentStatus.status -eq "running" -and
    $bridgeOwned -and
    $tunnelOwned -and
    $currentStatus.bridge.health.status -eq "ok" -and
    $currentStatus.tunnel.health.status -eq "ok"
)

if ($nativeHealthy) {
    [ordered]@{
        phase = "3"
        status = "ok"
        executable = $exePath
        bridge_config = $bridgeConfigPath
        projects_config = $projectsConfigPath
        env_file = $envFilePath
        lifecycle = "native-exe"
        note = "A healthy native EXE-managed lifecycle is already running; no legacy stop was attempted."
    } | ConvertTo-Json -Depth 6
    exit 0
}

$legacyStopped = $false
$nativeStopped = $false
try {
    if ($nativeOwned) {
        Write-Host "Stopping the existing native EXE-managed lifecycle"
        Invoke-NativeLifecycle -Arguments @("stop")
        $nativeStopped = $true
    } else {
        Write-Host "Stopping the legacy script-managed lifecycle"
        & pwsh -NoLogo -NoProfile -NonInteractive -File $legacyStop `
            -EnvFile $envFilePath
        if ($LASTEXITCODE -ne 0) {
            throw "legacy stop-all.ps1 failed with exit code $LASTEXITCODE"
        }
        $legacyStopped = $true
    }

    Write-Host "Starting the native EXE-managed lifecycle"
    Invoke-NativeLifecycle -Arguments @(
        "start",
        "--bridge-config", $bridgeConfigPath,
        "--projects-config", $projectsConfigPath,
        "--env-file", $envFilePath,
        "--startup-timeout", [string]$StartupTimeoutSec
    )

    Write-Host "Validating native lifecycle ownership and health"
    Invoke-NativeLifecycle -Arguments @("status")

    [ordered]@{
        phase = "3"
        status = "ok"
        executable = $exePath
        bridge_config = $bridgeConfigPath
        projects_config = $projectsConfigPath
        env_file = $envFilePath
        lifecycle = "native-exe"
        note = "Native EXE-managed services were left running for connector verification."
    } | ConvertTo-Json -Depth 6
    exit 0
} catch {
    $failure = $_.Exception.Message
    Write-Warning "Phase 3 native lifecycle validation failed: $failure"

    if ($nativeStopped) {
        Write-Warning "Attempting rollback to the native EXE-managed lifecycle"
        try {
            Invoke-NativeLifecycle -Arguments @(
                "start",
                "--bridge-config", $bridgeConfigPath,
                "--projects-config", $projectsConfigPath,
                "--env-file", $envFilePath,
                "--startup-timeout", [string]$StartupTimeoutSec
            )
        } catch {
            Write-Warning "Native lifecycle rollback also failed: $($_.Exception.Message)"
        }
    } elseif ($legacyStopped) {
        Write-Warning "Attempting rollback to the legacy script-managed lifecycle"
        try {
            & pwsh -NoLogo -NoProfile -NonInteractive -File $legacyStart `
                -BridgeConfig $bridgeConfigPath `
                -ProjectsConfig $projectsConfigPath `
                -EnvFile $envFilePath
            if ($LASTEXITCODE -ne 0) {
                throw "legacy start-all.ps1 failed with exit code $LASTEXITCODE"
            }
        } catch {
            Write-Warning "Legacy lifecycle rollback also failed: $($_.Exception.Message)"
        }
    }

    [ordered]@{
        phase = "3"
        status = "failed"
        error = $failure
        rollback_attempted = ($nativeStopped -or $legacyStopped)
    } | ConvertTo-Json -Depth 6
    exit 1
}
