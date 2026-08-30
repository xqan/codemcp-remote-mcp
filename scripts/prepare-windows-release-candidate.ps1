[CmdletBinding()]
param(
    [string]$InstallerDir,
    [string]$OutputRoot,
    [string]$Version = "0.1.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Windows release candidates must be prepared on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($InstallerDir)) {
    $InstallerDir = Join-Path $repositoryRoot ".local\installer-dist"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot ".local\release-candidate"
}
$InstallerDir = [System.IO.Path]::GetFullPath($InstallerDir)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)

$installer = Join-Path $InstallerDir "codemcp-remote-setup.exe"
$phase4Checksums = Join-Path $InstallerDir "SHA256SUMS.txt"
$validationScript = Join-Path $repositoryRoot "scripts\validate-clean-windows-release.ps1"
$phase6ValidationScript = Join-Path $repositoryRoot "scripts\validate-phase6-windows.ps1"
$validationDoc = Join-Path $repositoryRoot "docs\guides\clean-machine-validation.md"
$license = Join-Path $repositoryRoot "LICENSE"

foreach ($required in @($installer, $phase4Checksums, $validationScript, $phase6ValidationScript, $validationDoc, $license)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "required release-candidate input is missing: $required"
    }
}

$windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
    throw "Windows PowerShell 5.1 was not found for clean-machine script compatibility validation"
}
foreach ($scriptPath in @($validationScript, $phase6ValidationScript)) {
    $escapedScriptPath = $scriptPath.Replace("'", "''")
    $parseCommand = "[void][scriptblock]::Create([IO.File]::ReadAllText('$escapedScriptPath'))"
    & $windowsPowerShell -NoLogo -NoProfile -NonInteractive -Command $parseCommand
    if ($LASTEXITCODE -ne 0) {
        throw "release validation script is not compatible with the Windows PowerShell parser: $scriptPath"
    }
}

$checksumLine = @(
    Get-Content -LiteralPath $phase4Checksums -Encoding ASCII |
        Where-Object { $_ -match "^[0-9A-Fa-f]{64}\s+codemcp-remote-setup\.exe$" }
)
if ($checksumLine.Count -ne 1) {
    throw "Phase 4 SHA256SUMS.txt must contain exactly one codemcp-remote-setup.exe entry"
}
$expectedInstallerSha256 = ($checksumLine[0] -split "\s+")[0].ToLowerInvariant()
$actualInstallerSha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualInstallerSha256 -ne $expectedInstallerSha256) {
    throw "Phase 4 installer checksum mismatch"
}

$signature = Get-AuthenticodeSignature -LiteralPath $installer
$signatureStatus = [string]$signature.Status
if ($signatureStatus -notin @("Valid", "NotSigned")) {
    throw "installer Authenticode status is unsafe: $signatureStatus"
}

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($null -eq $git) {
    $git = Get-Command git -ErrorAction SilentlyContinue
}
if ($null -eq $git) {
    throw "Git is required to bind the release candidate to an exact source commit"
}
$sourceCommit = (& $git.Source -C $repositoryRoot rev-parse HEAD | Out-String).Trim()
$sourceBranch = (& $git.Source -C $repositoryRoot rev-parse --abbrev-ref HEAD | Out-String).Trim()
$sourceStatus = (& $git.Source -C $repositoryRoot status --porcelain | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "release candidate source commit could not be resolved"
}
if ([string]::IsNullOrWhiteSpace($sourceBranch)) {
    throw "release candidate source branch could not be resolved"
}
if (-not [string]::IsNullOrWhiteSpace($sourceStatus)) {
    throw "release candidate packaging requires a clean source worktree"
}

$candidateName = "codemcp-remote-v{0}-windows-x64" -f $Version
$candidateDir = Join-Path $OutputRoot $candidateName
$zipPath = Join-Path $OutputRoot ($candidateName + ".zip")
$zipHashPath = $zipPath + ".sha256"

Remove-Item -LiteralPath $candidateDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $zipHashPath -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $candidateDir | Out-Null

Copy-Item -LiteralPath $installer -Destination (Join-Path $candidateDir "codemcp-remote-setup.exe")
Copy-Item -LiteralPath $validationScript -Destination (Join-Path $candidateDir "validate-clean-windows-release.ps1")
Copy-Item -LiteralPath $phase6ValidationScript -Destination (Join-Path $candidateDir "validate-phase6-windows.ps1")
Copy-Item -LiteralPath $validationDoc -Destination (Join-Path $candidateDir "CLEAN-MACHINE-VALIDATION.md")
Copy-Item -LiteralPath $license -Destination (Join-Path $candidateDir "LICENSE")

$manifest = [ordered]@{
    product = "codemcp-remote"
    version = $Version
    platform = "windows-x64"
    phase = "5.5.7"
    source_git_commit = $sourceCommit
    source_git_branch = $sourceBranch
    source_worktree_dirty = $false
    installer_sha256 = $actualInstallerSha256
    authenticode_status = $signatureStatus
    recommended_transport = "cloudflare"
    bundled_transports = @(
        [ordered]@{
            provider = "cloudflare"
            executable = "cloudflared.exe"
            version = "2026.7.3"
            sha256 = "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841"
            license = "Apache-2.0"
        },
        [ordered]@{
            provider = "openai-tunnel"
            executable = "tunnel-client.exe"
            version = "v0.0.12"
            license = "Apache-2.0"
            role = "optional compatibility provider"
        }
    )
    runtime_prerequisites = @(
        "Windows 11 x64-compatible",
        "Git for Windows"
    )
    cloudflare_path_requires = @(
        "user-owned Cloudflare account/domain/tunnel configuration"
    )
    authenticated_chatgpt_path_requires = @(
        "compatible external mcp-auth-server deployment implementing mcp-rs-verification-v1"
    )
    not_required_at_runtime = @(
        "Python",
        "uv",
        "PowerShell 7",
        "WSL2",
        "codemcp-remote source repository",
        "separately installed cloudflared",
        "separately installed tunnel-client",
        "local or bundled mcp-auth-server runtime"
    )
    validation = @(
        "Run validate-clean-windows-release.ps1 on a clean Windows host or VM.",
        "After Prepare, run validate-phase6-windows.ps1 before the remaining ChatGPT remote Phase 6 cases."
    )
}
$manifestPath = Join-Path $candidateDir "release-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$checksumFiles = @(
    "codemcp-remote-setup.exe",
    "validate-clean-windows-release.ps1",
    "validate-phase6-windows.ps1",
    "CLEAN-MACHINE-VALIDATION.md",
    "LICENSE",
    "release-manifest.json"
)
$checksumLines = New-Object System.Collections.Generic.List[string]
foreach ($name in $checksumFiles) {
    $path = Join-Path $candidateDir $name
    $sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumLines.Add(("{0}  {1}" -f $sha256, $name))
}
$candidateChecksumPath = Join-Path $candidateDir "SHA256SUMS.txt"
$checksumLines | Set-Content -LiteralPath $candidateChecksumPath -Encoding ASCII

Compress-Archive -Path (Join-Path $candidateDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
$zipSha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
("{0}  {1}" -f $zipSha256, (Split-Path -Leaf $zipPath)) |
    Set-Content -LiteralPath $zipHashPath -Encoding ASCII

[ordered]@{
    status = "ok"
    phase = "5"
    candidate_dir = $candidateDir
    candidate_zip = $zipPath
    candidate_zip_sha256 = $zipSha256
    candidate_zip_sha256_file = $zipHashPath
    installer_sha256 = $actualInstallerSha256
    authenticode_status = $signatureStatus
    clean_machine_script = (Join-Path $candidateDir "validate-clean-windows-release.ps1")
    clean_machine_document = (Join-Path $candidateDir "CLEAN-MACHINE-VALIDATION.md")
} | ConvertTo-Json -Depth 5
