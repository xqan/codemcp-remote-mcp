[CmdletBinding()]
param(
    [string]$ISCCPath = "D:\Programs\Inno Setup 7\ISCC.exe",
    [string]$Version = "0.1.0",
    [string]$TunnelClientVersion = "v0.0.12",
    [string]$CloudflaredVersion = "2026.7.3",
    [switch]$ForceTunnelDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Windows release builds must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$installerOutput = Join-Path $repositoryRoot ".local\installer-dist"
$releaseOutput = Join-Path $repositoryRoot ".local\release-candidate"
$securityScript = Join-Path $repositoryRoot "scripts\validate-open-source-security.ps1"
$evidenceRoot = Join-Path $repositoryRoot ".local\release-evidence"
$productionUninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A26B4BA3-1D96-4F1A-95C4-9984C941A1E1}_is1"
$installerSmokeMode = if (Test-Path -LiteralPath $productionUninstallKey) {
    "isolated-existing-production-install"
} else {
    "production-clean-host"
}

function Invoke-ReleaseScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    & pwsh -NoLogo -NoProfile -NonInteractive -File $ScriptPath @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Save-SecurityEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $source = Join-Path $repositoryRoot ".local\security-audit"
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "security audit evidence directory was not created: $source"
    }
    $destination = Join-Path $evidenceRoot $Name
    Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
    return $destination
}

Remove-Item -LiteralPath $evidenceRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null

$installerArgs = @(
    "-OutputDir", $installerOutput,
    "-ISCCPath", $ISCCPath,
    "-AppVersion", $Version,
    "-TunnelClientVersion", $TunnelClientVersion,
    "-CloudflaredVersion", $CloudflaredVersion
)
if ($ForceTunnelDownload) {
    $installerArgs += "-ForceTunnelDownload"
}
Invoke-ReleaseScript `
    -Label "Windows installer build and smoke" `
    -ScriptPath (Join-Path $repositoryRoot "scripts\build-windows-installer.ps1") `
    -Arguments $installerArgs

$stagingPayload = Join-Path $repositoryRoot ".local\installer-work\exe-dist\codemcp-remote"
$installerPath = Join-Path $installerOutput "codemcp-remote-setup.exe"
if (-not (Test-Path -LiteralPath $stagingPayload -PathType Container)) {
    throw "installer build did not create the expected staging payload: $stagingPayload"
}
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "installer build did not create the expected installer: $installerPath"
}
$installerSha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()

$provenancePath = Join-Path $stagingPayload "BUILD_PROVENANCE.json"
$sourceCommitPath = Join-Path $stagingPayload "SOURCE_COMMIT.txt"
if (-not (Test-Path -LiteralPath $provenancePath -PathType Leaf)) {
    throw "staging payload is missing BUILD_PROVENANCE.json"
}
if (-not (Test-Path -LiteralPath $sourceCommitPath -PathType Leaf)) {
    throw "staging payload is missing SOURCE_COMMIT.txt"
}
$stagingSourceCommit = (Get-Content -LiteralPath $sourceCommitPath -Raw -Encoding ASCII).Trim()
if ($stagingSourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "staging SOURCE_COMMIT.txt is invalid"
}
$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($null -eq $git) {
    $git = Get-Command git -ErrorAction SilentlyContinue
}
if ($null -eq $git) {
    throw "Git is required to verify release source provenance"
}
$sourceCommit = (& $git.Source -C $repositoryRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "release source commit could not be resolved"
}
if ($stagingSourceCommit -ne $sourceCommit) {
    throw "staging payload source commit does not match the current release commit"
}

Invoke-ReleaseScript `
    -Label "Staging payload security audit" `
    -ScriptPath $securityScript `
    -Arguments @("-ArtifactPath", $stagingPayload)
$payloadEvidence = Save-SecurityEvidence -Name "staging-payload"

Invoke-ReleaseScript `
    -Label "Windows release candidate preparation" `
    -ScriptPath (Join-Path $repositoryRoot "scripts\prepare-windows-release-candidate.ps1") `
    -Arguments @("-InstallerDir", $installerOutput, "-OutputRoot", $releaseOutput, "-Version", $Version)

$candidateZip = Join-Path $releaseOutput ("codemcp-remote-v{0}-windows-x64.zip" -f $Version)
if (-not (Test-Path -LiteralPath $candidateZip -PathType Leaf)) {
    throw "release-candidate preparation did not create the expected ZIP: $candidateZip"
}
$candidateZipSha256 = (Get-FileHash -LiteralPath $candidateZip -Algorithm SHA256).Hash.ToLowerInvariant()

Invoke-ReleaseScript `
    -Label "Final RC security audit" `
    -ScriptPath $securityScript `
    -Arguments @("-ArtifactPath", $candidateZip, "-RequireArtifact")
$rcEvidence = Save-SecurityEvidence -Name "final-rc"

[ordered]@{
    status = "ok"
    version = $Version
    source_git_commit = $sourceCommit
    installer = $installerPath
    installer_sha256 = $installerSha256
    installer_smoke = "passed"
    installer_smoke_mode = $installerSmokeMode
    production_installer_smoke = if ($installerSmokeMode -eq "production-clean-host") {
        "passed"
    } else {
        "pending-clean-machine"
    }
    staging_payload = $stagingPayload
    staging_payload_audit = "passed"
    staging_payload_evidence = $payloadEvidence
    candidate_zip = $candidateZip
    candidate_zip_sha256 = $candidateZipSha256
    final_rc_audit = "passed"
    final_rc_evidence = $rcEvidence
    next_gate = "clean-machine-validation"
} | ConvertTo-Json -Depth 5
