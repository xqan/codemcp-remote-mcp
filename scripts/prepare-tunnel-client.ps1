[CmdletBinding()]
param(
    [string]$DestinationDir,
    [string]$CacheDir,
    [string]$Version = "v0.0.12",
    [switch]$ForceDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "tunnel-client Windows packaging must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($DestinationDir)) {
    $DestinationDir = Join-Path $repositoryRoot ".local\dist\codemcp-remote"
}
if ([string]::IsNullOrWhiteSpace($CacheDir)) {
    $CacheDir = Join-Path $repositoryRoot (".local\third-party\tunnel-client\{0}" -f $Version)
}

$DestinationDir = [System.IO.Path]::GetFullPath($DestinationDir)
$CacheDir = [System.IO.Path]::GetFullPath($CacheDir)
New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

$stem = "tunnel-client-{0}-windows-amd64" -f $Version
$baseUrl = "https://github.com/openai/tunnel-client/releases/download/{0}" -f $Version
$archiveName = "$stem.zip"
$sbomName = "$stem.spdx.json"
$checksumsName = "SHA256SUMS.txt"
$archivePath = Join-Path $CacheDir $archiveName
$sbomPath = Join-Path $CacheDir $sbomName
$checksumsPath = Join-Path $CacheDir $checksumsName

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

Get-ReleaseAsset -Url "$baseUrl/$archiveName" -Path $archivePath
Get-ReleaseAsset -Url "$baseUrl/$sbomName" -Path $sbomPath
Get-ReleaseAsset -Url "$baseUrl/$checksumsName" -Path $checksumsPath

$published = @{}
foreach ($line in Get-Content -LiteralPath $checksumsPath) {
    if ($line -match '^(?<sha>[0-9a-fA-F]{64})\s+\*?(?<name>.+)$') {
        $published[$Matches["name"].Trim()] = $Matches["sha"].ToLowerInvariant()
    }
}
foreach ($name in @($archiveName, $sbomName)) {
    if (-not $published.ContainsKey($name)) {
        throw "OpenAI tunnel-client checksum manifest does not contain $name"
    }
    $path = Join-Path $CacheDir $name
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne $published[$name]) {
        throw "OpenAI tunnel-client checksum mismatch for $name"
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$extractRoot = Join-Path $CacheDir "extract"
Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
$extractRootFull = [System.IO.Path]::GetFullPath($extractRoot + [System.IO.Path]::DirectorySeparatorChar)

$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    foreach ($entry in $archive.Entries) {
        $normalized = $entry.FullName.Replace('\', '/')
        if ([string]::IsNullOrWhiteSpace($normalized)) {
            continue
        }
        if ($normalized.StartsWith("/") -or
            $normalized -match '^[A-Za-z]:' -or
            @($normalized.Split('/')) -contains "..") {
            throw "unsafe tunnel-client archive entry: $normalized"
        }

        $unixMode = (($entry.ExternalAttributes -shr 16) -band 0xF000)
        if ($unixMode -eq 0xA000) {
            throw "symlink entries are not allowed in tunnel-client archive: $normalized"
        }

        if ($normalized.EndsWith("/")) {
            continue
        }

        $target = [System.IO.Path]::GetFullPath((Join-Path $extractRoot $normalized))
        if (-not $target.StartsWith($extractRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "tunnel-client archive entry escapes extraction root: $normalized"
        }
        $parent = Split-Path -Parent $target
        New-Item -ItemType Directory -Force -Path $parent | Out-Null

        $sourceStream = $entry.Open()
        try {
            $destinationStream = [System.IO.File]::Open(
                $target,
                [System.IO.FileMode]::Create,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            try {
                $sourceStream.CopyTo($destinationStream)
            } finally {
                $destinationStream.Dispose()
            }
        } finally {
            $sourceStream.Dispose()
        }
    }
} finally {
    $archive.Dispose()
}

$tunnelCandidates = @(
    Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "tunnel-client.exe"
)
if ($tunnelCandidates.Count -ne 1) {
    throw "expected exactly one tunnel-client.exe in the verified OpenAI archive"
}
$licenseCandidates = @(
    Get-ChildItem -LiteralPath $extractRoot -Recurse -File |
        Where-Object { $_.Name -in @("LICENSE", "LICENSE.txt", "LICENSE.TXT") }
)
if ($licenseCandidates.Count -lt 1) {
    throw "OpenAI tunnel-client archive did not contain a license file"
}

$embeddedSbom = @(
    Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "*.spdx.json"
)
if ($embeddedSbom.Count -gt 0) {
    $downloadedSbomHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sbomPath).Hash
    $matchingEmbedded = @(
        $embeddedSbom | Where-Object {
            (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash -eq $downloadedSbomHash
        }
    )
    if ($matchingEmbedded.Count -eq 0) {
        throw "embedded tunnel-client SPDX sidecar does not match the published release sidecar"
    }
}

$destinationExe = Join-Path $DestinationDir "tunnel-client.exe"
Copy-Item -LiteralPath $tunnelCandidates[0].FullName -Destination $destinationExe -Force

$thirdPartyDir = Join-Path $DestinationDir "THIRD_PARTY\tunnel-client"
New-Item -ItemType Directory -Force -Path $thirdPartyDir | Out-Null
Copy-Item -LiteralPath $licenseCandidates[0].FullName -Destination (Join-Path $thirdPartyDir "LICENSE") -Force
Copy-Item -LiteralPath $sbomPath -Destination (Join-Path $thirdPartyDir "$stem.spdx.json") -Force
Copy-Item -LiteralPath $checksumsPath -Destination (Join-Path $thirdPartyDir "UPSTREAM-SHA256SUMS.txt") -Force

$versionOutput = (& $destinationExe --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch [regex]::Escape($Version.TrimStart("v"))) {
    throw "packaged tunnel-client version check failed: $versionOutput"
}

$archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
$binarySha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationExe).Hash.ToLowerInvariant()
$notice = @"
Third-party software bundled with codemcp-remote

OpenAI Secure MCP Tunnel client
Version: $Version
Source: https://github.com/openai/tunnel-client
Release: https://github.com/openai/tunnel-client/releases/tag/$Version
License: Apache License 2.0
Installed license text: THIRD_PARTY\tunnel-client\LICENSE
Upstream SPDX evidence: THIRD_PARTY\tunnel-client\$stem.spdx.json
Upstream archive SHA-256: $archiveSha256
Installed tunnel-client.exe SHA-256: $binarySha256

codemcp-remote is not affiliated with or endorsed by OpenAI.
"@
$notice | Set-Content -LiteralPath (Join-Path $DestinationDir "THIRD_PARTY_NOTICES.txt") -Encoding utf8

$distributionChecksums = Join-Path $DestinationDir "SHA256SUMS.txt"
$existingLines = @()
if (Test-Path -LiteralPath $distributionChecksums -PathType Leaf) {
    $existingLines = @(
        Get-Content -LiteralPath $distributionChecksums |
            Where-Object { $_ -notmatch '\s+tunnel-client\.exe$' }
    )
}
$updatedLines = @($existingLines) + ("{0}  tunnel-client.exe" -f $binarySha256)
$updatedLines | Set-Content -LiteralPath $distributionChecksums -Encoding ascii

[ordered]@{
    status = "ok"
    version = $Version
    source = "openai/tunnel-client"
    license = "Apache-2.0"
    archive = $archivePath
    archive_sha256 = $archiveSha256
    executable = $destinationExe
    executable_sha256 = $binarySha256
    version_output = $versionOutput
    sbom = Join-Path $thirdPartyDir "$stem.spdx.json"
} | ConvertTo-Json -Depth 4
