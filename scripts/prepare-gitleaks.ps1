[CmdletBinding()]
param(
    [string]$DestinationDir,
    [string]$Version = "8.30.0",
    [switch]$ForceDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "the local Gitleaks preparation helper currently supports Windows x64 only"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($DestinationDir)) {
    $DestinationDir = Join-Path $repositoryRoot (".local\tools\gitleaks\{0}" -f $Version)
}
$DestinationDir = [System.IO.Path]::GetFullPath($DestinationDir)

$pinned = @{
    "8.30.0" = @{
        asset = "gitleaks_8.30.0_windows_x64.zip"
        sha256 = "54fe94f644b832dd08e8c3a5915efb3bfa862386d59fb27ca0792cb687a83573"
    }
}
if (-not $pinned.ContainsKey($Version)) {
    throw "Gitleaks version is not pinned for the release audit: $Version"
}
$pin = $pinned[$Version]

$cacheDir = Join-Path $repositoryRoot (".local\third-party\gitleaks\{0}" -f $Version)
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null

$archivePath = Join-Path $cacheDir $pin.asset
$releaseUrl = "https://github.com/gitleaks/gitleaks/releases/download/v{0}/{1}" -f $Version, $pin.asset
if ($ForceDownload -or -not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    $temporary = "$archivePath.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -Uri $releaseUrl -OutFile $temporary -UseBasicParsing
        Move-Item -LiteralPath $temporary -Destination $archivePath -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

$archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
if ($archiveSha256 -ne $pin.sha256) {
    throw "Gitleaks archive checksum mismatch"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $exeEntries = @(
        $archive.Entries | Where-Object {
            $_.FullName.Replace("\", "/") -match '(^|/)gitleaks\.exe$'
        }
    )
    $licenseEntries = @(
        $archive.Entries | Where-Object {
            $_.FullName.Replace("\", "/") -match '(^|/)LICENSE$'
        }
    )
    if ($exeEntries.Count -ne 1 -or $licenseEntries.Count -lt 1) {
        throw "verified Gitleaks archive does not contain the expected executable/license"
    }

    $exePath = Join-Path $DestinationDir "gitleaks.exe"
    $licensePath = Join-Path $DestinationDir "LICENSE"
    foreach ($pair in @(
        @($exeEntries[0], $exePath),
        @($licenseEntries[0], $licensePath)
    )) {
        $entry = $pair[0]
        $target = $pair[1]
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

$versionOutput = (& (Join-Path $DestinationDir "gitleaks.exe") version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch [regex]::Escape($Version)) {
    throw "Gitleaks version smoke failed: $versionOutput"
}

[ordered]@{
    status = "ok"
    version = $Version
    source = "gitleaks/gitleaks"
    archive = $archivePath
    archive_sha256 = $archiveSha256
    executable = Join-Path $DestinationDir "gitleaks.exe"
    license = Join-Path $DestinationDir "LICENSE"
} | ConvertTo-Json -Depth 4
