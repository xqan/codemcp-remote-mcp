[CmdletBinding()]
param(
    [string]$DestinationDir,
    [string]$CacheDir,
    [string]$Version = "2026.7.3",
    [switch]$ForceDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "cloudflared Windows packaging must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($DestinationDir)) {
    $DestinationDir = Join-Path $repositoryRoot ".local\dist\codemcp-remote"
}
if ([string]::IsNullOrWhiteSpace($CacheDir)) {
    $CacheDir = Join-Path $repositoryRoot (".local\third-party\cloudflared\{0}" -f $Version)
}

$pinnedChecksums = @{
    "2026.7.3" = "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841"
}
if (-not $pinnedChecksums.ContainsKey($Version)) {
    throw "cloudflared version is not pinned for packaging: $Version"
}
$expectedSha256 = $pinnedChecksums[$Version]

$DestinationDir = [System.IO.Path]::GetFullPath($DestinationDir)
$CacheDir = [System.IO.Path]::GetFullPath($CacheDir)
New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

$assetName = "cloudflared-windows-amd64.exe"
$baseUrl = "https://github.com/cloudflare/cloudflared/releases/download/{0}" -f $Version
$assetPath = Join-Path $CacheDir $assetName
$licensePath = Join-Path $CacheDir "LICENSE"
$licenseUrl = "https://raw.githubusercontent.com/cloudflare/cloudflared/{0}/LICENSE" -f $Version

function Get-ReleaseAsset {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ((Test-Path -LiteralPath $Path -PathType Leaf) -and -not $ForceDownload) {
        return
    }
    $temporary = "$Path.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -Uri $Url -OutFile $temporary -UseBasicParsing
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

Get-ReleaseAsset -Url "$baseUrl/$assetName" -Path $assetPath
Get-ReleaseAsset -Url $licenseUrl -Path $licensePath

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $assetPath).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "cloudflared checksum mismatch for $assetName"
}

$destinationExe = Join-Path $DestinationDir "cloudflared.exe"
Copy-Item -LiteralPath $assetPath -Destination $destinationExe -Force

$thirdPartyDir = Join-Path $DestinationDir "THIRD_PARTY\cloudflared"
New-Item -ItemType Directory -Force -Path $thirdPartyDir | Out-Null
Copy-Item -LiteralPath $licensePath -Destination (Join-Path $thirdPartyDir "LICENSE") -Force

$versionOutput = (& $destinationExe --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch [regex]::Escape($Version)) {
    throw "packaged cloudflared version check failed: $versionOutput"
}

$notice = @"
Cloudflare Tunnel client (cloudflared)
Version: $Version
Source: https://github.com/cloudflare/cloudflared
Release: https://github.com/cloudflare/cloudflared/releases/tag/$Version
Asset: $assetName
License: Apache License 2.0
Installed license text: THIRD_PARTY\cloudflared\LICENSE
Installed cloudflared.exe SHA-256: $actualSha256
"@
$notice | Set-Content -LiteralPath (Join-Path $thirdPartyDir "NOTICE.txt") -Encoding utf8

$distributionChecksums = Join-Path $DestinationDir "SHA256SUMS.txt"
$existingLines = @()
if (Test-Path -LiteralPath $distributionChecksums -PathType Leaf) {
    $existingLines = @(
        Get-Content -LiteralPath $distributionChecksums |
            Where-Object { $_ -notmatch '\s+cloudflared\.exe$' }
    )
}
$updatedLines = @($existingLines) + ("{0}  cloudflared.exe" -f $actualSha256)
$updatedLines | Set-Content -LiteralPath $distributionChecksums -Encoding ascii

[ordered]@{
    status = "ok"
    version = $Version
    source = "cloudflare/cloudflared"
    license = "Apache-2.0"
    executable = $destinationExe
    executable_sha256 = $actualSha256
    version_output = $versionOutput
    license_path = Join-Path $thirdPartyDir "LICENSE"
    notice_path = Join-Path $thirdPartyDir "NOTICE.txt"
} | ConvertTo-Json -Depth 4
