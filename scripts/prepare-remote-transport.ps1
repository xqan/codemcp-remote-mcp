[CmdletBinding()]
param(
    [string]$DestinationDir,
    [string]$CloudflaredVersion = "2026.7.3",
    [string]$OpenAITunnelClientVersion = "v0.0.12",
    [switch]$ForceDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "remote transport Windows packaging must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($DestinationDir)) {
    $DestinationDir = Join-Path $repositoryRoot ".local\dist\codemcp-remote"
}
$DestinationDir = [System.IO.Path]::GetFullPath($DestinationDir)

function Invoke-PreparationScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $output = (& pwsh -NoLogo -NoProfile -NonInteractive -File $ScriptPath @Arguments | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "$Label preparation failed with exit code $LASTEXITCODE"
    }
    try {
        $result = $output | ConvertFrom-Json
    } catch {
        throw "$Label preparation did not return valid JSON: $output"
    }
    if ($result.status -ne "ok") {
        throw "$Label preparation did not report status=ok"
    }
    return $result
}

$openAIArgs = @(
    "-DestinationDir", $DestinationDir,
    "-Version", $OpenAITunnelClientVersion
)
$cloudflareArgs = @(
    "-DestinationDir", $DestinationDir,
    "-Version", $CloudflaredVersion
)
if ($ForceDownload) {
    $openAIArgs += "-ForceDownload"
    $cloudflareArgs += "-ForceDownload"
}

$openAI = Invoke-PreparationScript `
    -ScriptPath (Join-Path $repositoryRoot "scripts\prepare-tunnel-client.ps1") `
    -Arguments $openAIArgs `
    -Label "OpenAI tunnel-client"
$cloudflare = Invoke-PreparationScript `
    -ScriptPath (Join-Path $repositoryRoot "scripts\prepare-cloudflared.ps1") `
    -Arguments $cloudflareArgs `
    -Label "cloudflared"

$codemcpVersion = "0.3.0"
$codemcpWheelSha256 = "a56123f6e1544aed55dbfd1b4946fc2583222b4104a82d8a2171d8c1621cd32a"
$codemcpSdistSha256 = "a28161aa86176cebd1861e7c134ac98ab1762849d75b46915e0a9fc4ef6efae7"
$codemcpDistInfo = @(
    Get-ChildItem -LiteralPath $DestinationDir -Recurse -Directory `
        -Filter ("codemcp-{0}.dist-info" -f $codemcpVersion) -ErrorAction Stop
)
if ($codemcpDistInfo.Count -ne 1) {
    throw "expected exactly one packaged codemcp $codemcpVersion distribution metadata directory"
}
$codemcpMetadataPath = Join-Path $codemcpDistInfo[0].FullName "METADATA"
$codemcpLicensePath = Join-Path $codemcpDistInfo[0].FullName "licenses\LICENSE.txt"
if (-not (Test-Path -LiteralPath $codemcpMetadataPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $codemcpLicensePath -PathType Leaf)) {
    throw "packaged codemcp distribution is missing metadata or its declared license file"
}
$codemcpMetadata = Get-Content -LiteralPath $codemcpMetadataPath -Raw -Encoding UTF8
if ($codemcpMetadata -notmatch '(?m)^Name: codemcp\r?$' -or
    $codemcpMetadata -notmatch '(?m)^Version: 0\.3\.0\r?$' -or
    $codemcpMetadata -notmatch '(?m)^License: MIT\r?$' -or
    $codemcpMetadata -notmatch '(?m)^License-File: LICENSE\.txt\r?$') {
    throw "packaged codemcp metadata does not match the audited 0.3.0 distribution"
}
$codemcpLicense = Get-Content -LiteralPath $codemcpLicensePath -Raw -Encoding UTF8
if ($codemcpLicense -notmatch 'Apache License\s+Version 2\.0') {
    throw "packaged codemcp 0.3.0 license file is not the audited Apache-2.0 text"
}
$lockText = Get-Content -LiteralPath (Join-Path $repositoryRoot "bridge\uv.lock") -Raw -Encoding UTF8
if ($lockText -notmatch [regex]::Escape($codemcpWheelSha256) -or
    $lockText -notmatch [regex]::Escape($codemcpSdistSha256)) {
    throw "bridge lockfile no longer contains the audited codemcp 0.3.0 artifact hashes"
}
$codemcpThirdPartyDir = Join-Path $DestinationDir "THIRD_PARTY\codemcp"
New-Item -ItemType Directory -Force -Path $codemcpThirdPartyDir | Out-Null
Copy-Item -LiteralPath $codemcpLicensePath -Destination (Join-Path $codemcpThirdPartyDir "LICENSE.txt") -Force
$codemcpLicenseSha256 = (
    Get-FileHash -LiteralPath (Join-Path $codemcpThirdPartyDir "LICENSE.txt") -Algorithm SHA256
).Hash.ToLowerInvariant()
$codemcpNotice = @"
codemcp
Version: $codemcpVersion
Source: https://pypi.org/project/codemcp/$codemcpVersion/
Locked PyPI wheel SHA-256: $codemcpWheelSha256
Locked PyPI sdist SHA-256: $codemcpSdistSha256
Distribution metadata License field: MIT
Bundled License-File: Apache License 2.0
Installed license text: THIRD_PARTY\codemcp\LICENSE.txt
Installed license text SHA-256: $codemcpLicenseSha256
Note: upstream codemcp 0.3.0 distribution metadata and its bundled License-File disagree; codemcp-remote preserves the bundled license text verbatim and records both facts.
"@
$codemcpNotice | Set-Content -LiteralPath (Join-Path $codemcpThirdPartyDir "NOTICE.txt") -Encoding utf8

$pyInstallerNoticePath = Join-Path $DestinationDir "THIRD_PARTY\pyinstaller\NOTICE.txt"
$pyInstallerCopyingPath = Join-Path $DestinationDir "THIRD_PARTY\pyinstaller\COPYING.txt"
$buildProvenancePath = Join-Path $DestinationDir "BUILD_PROVENANCE.json"
foreach ($requiredBuildEvidence in @($pyInstallerNoticePath, $pyInstallerCopyingPath, $buildProvenancePath)) {
    if (-not (Test-Path -LiteralPath $requiredBuildEvidence -PathType Leaf)) {
        throw "packaged build-tool provenance/license evidence is missing: $requiredBuildEvidence"
    }
}
$pyInstallerNotice = Get-Content -LiteralPath $pyInstallerNoticePath -Raw -Encoding UTF8
if ($pyInstallerNotice -notmatch 'GPL-2\.0-or-later WITH Bootloader-exception') {
    throw "packaged PyInstaller notice does not describe the audited bootloader exception"
}

$notice = @"
Third-party software bundled with codemcp-remote

$codemcpNotice

$pyInstallerNotice

Cloudflare Tunnel client (cloudflared)
Version: $($cloudflare.version)
Source: https://github.com/cloudflare/cloudflared
License: Apache License 2.0
Installed license text: THIRD_PARTY\cloudflared\LICENSE
Installed cloudflared.exe SHA-256: $($cloudflare.executable_sha256)

OpenAI Secure MCP Tunnel client
Version: $($openAI.version)
Source: https://github.com/openai/tunnel-client
License: Apache License 2.0
Installed license text: THIRD_PARTY\tunnel-client\LICENSE
Installed tunnel-client.exe SHA-256: $($openAI.executable_sha256)

codemcp-remote is not affiliated with or endorsed by codemcp, PyInstaller, Cloudflare, or OpenAI.
"@
$notice | Set-Content -LiteralPath (Join-Path $DestinationDir "THIRD_PARTY_NOTICES.txt") -Encoding utf8

[ordered]@{
    status = "ok"
    recommended_provider = "cloudflare"
    cloudflare = $cloudflare
    openai_tunnel = $openAI
    notices = Join-Path $DestinationDir "THIRD_PARTY_NOTICES.txt"
} | ConvertTo-Json -Depth 6
