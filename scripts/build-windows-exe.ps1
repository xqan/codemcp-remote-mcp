[CmdletBinding()]
param(
    [string]$DistDir,
    [string]$WorkDir,
    [switch]$SkipSmoke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Windows EXE builds must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bridgeProject = Join-Path $repositoryRoot "bridge"
$bridgeSrc = Join-Path $bridgeProject "src"
$entrypoint = Join-Path $repositoryRoot "scripts\windows_entrypoint.py"

$git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($null -eq $git) {
    $git = Get-Command git -ErrorAction SilentlyContinue
}
if ($null -eq $git) {
    throw "Git is required to bind the Windows release artifact to an exact source commit"
}
$sourceCommit = (& $git.Source -C $repositoryRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "git rev-parse HEAD failed while preparing Windows build provenance"
}
$sourceBranch = (& $git.Source -C $repositoryRoot rev-parse --abbrev-ref HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceBranch)) {
    throw "git rev-parse --abbrev-ref HEAD failed while preparing Windows build provenance"
}
$sourceStatus = (& $git.Source -C $repositoryRoot status --porcelain | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "git status --porcelain failed while preparing Windows build provenance"
}
if (-not [string]::IsNullOrWhiteSpace($sourceStatus)) {
    throw "Windows release build requires a clean source worktree"
}

$pyInstallerVersion = "6.22.2"
$pyInstallerFileName = "pyinstaller-6.22.2-py3-none-win_amd64.whl"
$pyInstallerSha256 = "9b990fa6bbe143572f06644a984ad0d7aa2e2ccc6929d4916031343a5888e9a7"
$pyInstallerHooksVersion = "2026.6"
$pyInstallerHooksFileName = "pyinstaller_hooks_contrib-2026.6-py3-none-any.whl"
$pyInstallerHooksSha256 = "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3"
$altgraphVersion = "0.17.5"
$altgraphFileName = "altgraph-0.17.5-py2.py3-none-any.whl"
$altgraphSha256 = "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597"
$pefileVersion = "2024.8.26"
$pefileFileName = "pefile-2024.8.26-py3-none-any.whl"
$pefileSha256 = "76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f"
$pywin32CtypesVersion = "0.2.3"
$pywin32CtypesFileName = "pywin32_ctypes-0.2.3-py3-none-any.whl"
$pywin32CtypesSha256 = "8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8"
$packagingVersion = "26.3"
$packagingFileName = "packaging-26.3-py3-none-any.whl"
$packagingSha256 = "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c"
$setuptoolsVersion = "84.0.0"
$setuptoolsFileName = "setuptools-84.0.0-py3-none-any.whl"
$setuptoolsSha256 = "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"

if ([string]::IsNullOrWhiteSpace($DistDir)) {
    $DistDir = Join-Path $repositoryRoot ".local\dist"
}
if ([string]::IsNullOrWhiteSpace($WorkDir)) {
    $WorkDir = Join-Path $repositoryRoot ".local\pyinstaller"
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv was not found on PATH"
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne
    [System.Runtime.InteropServices.Architecture]::X64) {
    throw "the v0.1.0 Windows release build currently supports x64 hosts only"
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$buildToolCache = Join-Path $repositoryRoot ".local\third-party\python-build-tools"
$prepareWheel = Join-Path $repositoryRoot "scripts\prepare-pypi-wheel.ps1"

function Resolve-VerifiedBuildWheel {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Project,
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [string]$FileName,
        [Parameter(Mandatory = $true)]
        [string]$Sha256
    )

    $resultJson = (& pwsh -NoLogo -NoProfile -NonInteractive `
        -File $prepareWheel `
        -Project $Project `
        -Version $Version `
        -FileName $FileName `
        -Sha256 $Sha256 `
        -DestinationDir $buildToolCache | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "verified build-tool wheel preparation failed: $Project $Version"
    }
    try {
        $result = $resultJson | ConvertFrom-Json
    } catch {
        throw "verified build-tool wheel preparation returned invalid JSON: $Project $Version"
    }
    if ($result.status -ne "ok" -or -not (Test-Path -LiteralPath $result.artifact -PathType Leaf)) {
        throw "verified build-tool wheel was not prepared: $Project $Version"
    }
    return [string]$result.artifact
}

$pyInstallerWheel = Resolve-VerifiedBuildWheel `
    -Project "pyinstaller" `
    -Version $pyInstallerVersion `
    -FileName $pyInstallerFileName `
    -Sha256 $pyInstallerSha256
$pyInstallerHooksWheel = Resolve-VerifiedBuildWheel `
    -Project "pyinstaller-hooks-contrib" `
    -Version $pyInstallerHooksVersion `
    -FileName $pyInstallerHooksFileName `
    -Sha256 $pyInstallerHooksSha256
$altgraphWheel = Resolve-VerifiedBuildWheel `
    -Project "altgraph" `
    -Version $altgraphVersion `
    -FileName $altgraphFileName `
    -Sha256 $altgraphSha256
$pefileWheel = Resolve-VerifiedBuildWheel `
    -Project "pefile" `
    -Version $pefileVersion `
    -FileName $pefileFileName `
    -Sha256 $pefileSha256
$pywin32CtypesWheel = Resolve-VerifiedBuildWheel `
    -Project "pywin32-ctypes" `
    -Version $pywin32CtypesVersion `
    -FileName $pywin32CtypesFileName `
    -Sha256 $pywin32CtypesSha256
$packagingWheel = Resolve-VerifiedBuildWheel `
    -Project "packaging" `
    -Version $packagingVersion `
    -FileName $packagingFileName `
    -Sha256 $packagingSha256
$setuptoolsWheel = Resolve-VerifiedBuildWheel `
    -Project "setuptools" `
    -Version $setuptoolsVersion `
    -FileName $setuptoolsFileName `
    -Sha256 $setuptoolsSha256

$pyInstallerArgs = @(
    "run",
    "--project", $bridgeProject,
    "--with", $pyInstallerWheel,
    "--with", $pyInstallerHooksWheel,
    "--with", $altgraphWheel,
    "--with", $pefileWheel,
    "--with", $pywin32CtypesWheel,
    "--with", $packagingWheel,
    "--with", $setuptoolsWheel,
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--console",
    "--name", "codemcp-remote",
    "--paths", $bridgeSrc,
    "--collect-submodules", "codemcp",
    "--copy-metadata", "codemcp",
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $WorkDir,
    $entrypoint
)

$phase3Files = @(
    (Join-Path $bridgeSrc "codemcp_bridge\lifecycle.py"),
    (Join-Path $bridgeSrc "codemcp_bridge\main.py"),
    (Join-Path $bridgeProject "tests\test_phase3_lifecycle.py")
)
& $uv.Source run --project $bridgeProject ruff format --check @phase3Files
if ($LASTEXITCODE -ne 0) {
    throw "Phase 3 scoped Ruff format check failed with exit code $LASTEXITCODE"
}

$phase3Tests = Join-Path $bridgeProject "tests\test_phase3_lifecycle.py"
& $uv.Source run --project $bridgeProject pytest $phase3Tests -q
if ($LASTEXITCODE -ne 0) {
    throw "Phase 3 lifecycle tests failed with exit code $LASTEXITCODE"
}

& $uv.Source @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$appDir = Join-Path $DistDir "codemcp-remote"
$exePath = Join-Path $appDir "codemcp-remote.exe"
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "expected executable was not created: $exePath"
}

$configDir = Join-Path $appDir "config"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
Copy-Item -LiteralPath (Join-Path $repositoryRoot "config\bridge.example.toml") -Destination $configDir -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "config\projects.example.toml") -Destination $configDir -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "LICENSE") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\codemcp-start.cmd") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\codemcp-stop.cmd") -Destination $appDir -Force

$pyInstallerLicenseExtract = Join-Path $WorkDir "pyinstaller-license"
Remove-Item -LiteralPath $pyInstallerLicenseExtract -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $pyInstallerLicenseExtract | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($pyInstallerWheel, $pyInstallerLicenseExtract)
$pyInstallerCopyingCandidates = @(
    Get-ChildItem -LiteralPath $pyInstallerLicenseExtract -Recurse -File -ErrorAction Stop |
        Where-Object { $_.Name -ieq "COPYING.txt" }
)
if ($pyInstallerCopyingCandidates.Count -ne 1) {
    throw "expected exactly one COPYING.txt in the verified PyInstaller wheel"
}
$pyInstallerCopying = Get-Content -LiteralPath $pyInstallerCopyingCandidates[0].FullName -Raw -Encoding UTF8
if ($pyInstallerCopying -notmatch '(?is)bootloader.{0,80}exception') {
    throw "verified PyInstaller wheel COPYING.txt does not contain the expected bootloader exception"
}
$pyInstallerThirdPartyDir = Join-Path $appDir "THIRD_PARTY\pyinstaller"
New-Item -ItemType Directory -Force -Path $pyInstallerThirdPartyDir | Out-Null
Copy-Item -LiteralPath $pyInstallerCopyingCandidates[0].FullName `
    -Destination (Join-Path $pyInstallerThirdPartyDir "COPYING.txt") -Force
$pyInstallerNotice = @"
PyInstaller
Version: $pyInstallerVersion
Build input: $pyInstallerFileName
Build input SHA-256: $pyInstallerSha256
License evidence: THIRD_PARTY\pyinstaller\COPYING.txt
License identifier: GPL-2.0-or-later WITH Bootloader-exception
Use in codemcp-remote: PyInstaller bootloader/build output only; the verified upstream COPYING.txt is preserved verbatim.
"@
$pyInstallerNotice | Set-Content `
    -LiteralPath (Join-Path $pyInstallerThirdPartyDir "NOTICE.txt") -Encoding utf8

$buildInputs = @(
    [ordered]@{ project = "pyinstaller"; version = $pyInstallerVersion; file = $pyInstallerFileName; sha256 = $pyInstallerSha256 },
    [ordered]@{ project = "pyinstaller-hooks-contrib"; version = $pyInstallerHooksVersion; file = $pyInstallerHooksFileName; sha256 = $pyInstallerHooksSha256 },
    [ordered]@{ project = "altgraph"; version = $altgraphVersion; file = $altgraphFileName; sha256 = $altgraphSha256 },
    [ordered]@{ project = "pefile"; version = $pefileVersion; file = $pefileFileName; sha256 = $pefileSha256 },
    [ordered]@{ project = "pywin32-ctypes"; version = $pywin32CtypesVersion; file = $pywin32CtypesFileName; sha256 = $pywin32CtypesSha256 },
    [ordered]@{ project = "packaging"; version = $packagingVersion; file = $packagingFileName; sha256 = $packagingSha256 },
    [ordered]@{ project = "setuptools"; version = $setuptoolsVersion; file = $setuptoolsFileName; sha256 = $setuptoolsSha256 }
)
$buildProvenancePath = Join-Path $appDir "BUILD_PROVENANCE.json"
[ordered]@{
    schema = "codemcp-remote-build-provenance-v1"
    platform = "windows-x64"
    source = [ordered]@{
        git_commit = $sourceCommit
        git_branch = $sourceBranch
        worktree_dirty = $false
    }
    build_tools = $buildInputs
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $buildProvenancePath -Encoding utf8
$sourceCommitPath = Join-Path $appDir "SOURCE_COMMIT.txt"
$sourceCommit | Set-Content -LiteralPath $sourceCommitPath -Encoding ascii -NoNewline

$exeSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $exePath).Hash.ToLowerInvariant()
$sha256File = Join-Path $appDir "SHA256SUMS.txt"
("{0}  codemcp-remote.exe" -f $exeSha256) | Set-Content -LiteralPath $sha256File -Encoding ascii

if (-not $SkipSmoke) {
    & $exePath --version
    if ($LASTEXITCODE -ne 0) {
        throw "frozen Bridge version check failed with exit code $LASTEXITCODE"
    }
    & $exePath check
    if ($LASTEXITCODE -ne 0) {
        throw "frozen Bridge check failed with exit code $LASTEXITCODE"
    }

    $workerSmoke = Join-Path $repositoryRoot "tests\integration\executable_smoke.py"
    & $uv.Source run --project $bridgeProject python $workerSmoke $exePath $repositoryRoot
    if ($LASTEXITCODE -ne 0) {
        throw "frozen worker mutation smoke failed with exit code $LASTEXITCODE"
    }

    $lifecycleSmokeRoot = Join-Path $WorkDir "lifecycle-smoke"
    Remove-Item -LiteralPath $lifecycleSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
    & $exePath status --home $lifecycleSmokeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "frozen lifecycle status smoke failed with exit code $LASTEXITCODE"
    }
}

[ordered]@{
    status = "ok"
    pyinstaller_version = $pyInstallerVersion
    executable = $exePath
    distribution_dir = $appDir
    sha256 = $exeSha256
    sha256_file = $sha256File
    smoke = if ($SkipSmoke) { "skipped" } else { "passed" }
} | ConvertTo-Json -Depth 4
