[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$FileName,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string]$Sha256,
    [Parameter(Mandatory = $true)]
    [string]$DestinationDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Project -notmatch '^[A-Za-z0-9._-]+$' -or
    $Version -notmatch '^[A-Za-z0-9._+-]+$' -or
    $FileName -notmatch '^[A-Za-z0-9._+-]+\.whl$') {
    throw "invalid PyPI artifact identity"
}

$expectedSha256 = $Sha256.ToLowerInvariant()
$DestinationDir = [System.IO.Path]::GetFullPath($DestinationDir)
New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null

$artifactPath = Join-Path $DestinationDir $FileName
$metadataUrl = "https://pypi.org/pypi/{0}/{1}/json" -f $Project, $Version

try {
    $metadata = Invoke-RestMethod -Uri $metadataUrl -Method Get
} catch {
    throw "failed to fetch PyPI metadata for $Project $Version"
}

$candidates = @(
    $metadata.urls | Where-Object {
        [string]$_.filename -eq $FileName -and [string]$_.packagetype -eq "bdist_wheel"
    }
)
if ($candidates.Count -ne 1) {
    throw "PyPI metadata did not contain exactly one expected wheel: $FileName"
}

$publishedSha256 = ([string]$candidates[0].digests.sha256).ToLowerInvariant()
if ($publishedSha256 -ne $expectedSha256) {
    throw "PyPI published SHA-256 does not match the repository pin for $FileName"
}

if (Test-Path -LiteralPath $artifactPath -PathType Leaf) {
    $cachedSha256 = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($cachedSha256 -ne $expectedSha256) {
        throw "cached PyPI wheel checksum mismatch: $artifactPath"
    }
} else {
    $temporary = "$artifactPath.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -Uri ([string]$candidates[0].url) -OutFile $temporary -UseBasicParsing
        $downloadSha256 = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($downloadSha256 -ne $expectedSha256) {
            throw "downloaded PyPI wheel checksum mismatch: $FileName"
        }
        Move-Item -LiteralPath $temporary -Destination $artifactPath -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

[ordered]@{
    status = "ok"
    project = $Project
    version = $Version
    filename = $FileName
    sha256 = $expectedSha256
    artifact = $artifactPath
    source = "pypi-json-verified-wheel"
} | ConvertTo-Json -Depth 4
