[CmdletBinding()]
param(
    [string]$OutputDir,
    [string]$ISCCPath = "D:\Programs\Inno Setup 7\ISCC.exe",
    [string]$AppVersion = "0.1.0",
    [string]$TunnelClientVersion = "v0.0.12",
    [string]$CloudflaredVersion = "2026.7.3",
    [switch]$SkipAppBuild,
    [switch]$SkipSmoke,
    [switch]$ForceTunnelDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Windows installer builds must run on Windows"
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$installerWorkDir = Join-Path $repositoryRoot ".local\installer-work"
$appDistDir = Join-Path $installerWorkDir "exe-dist"
$appWorkDir = Join-Path $installerWorkDir "pyinstaller"
$appDir = Join-Path $appDistDir "codemcp-remote"
$mainExe = Join-Path $appDir "codemcp-remote.exe"
$installerScript = Join-Path $repositoryRoot "scripts\codemcp-remote.iss"
$productionUninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A26B4BA3-1D96-4F1A-95C4-9984C941A1E1}_is1"
$smokeAppId = "{67A907D8-6E0E-4F58-85D1-443C5AA41A42}"
$smokeUninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($smokeAppId)_is1"
$smokeProductRegistryKey = "Software\codemcp-remote-installer-smoke"
$smokeProductRegistryPath = "HKCU:\$smokeProductRegistryKey"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repositoryRoot ".local\installer-dist"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

function Resolve-ISCC {
    param([string]$RequestedPath)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $resolved = [System.IO.Path]::GetFullPath($RequestedPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "ISCC.exe was not found: $resolved"
        }
        return $resolved
    }

    foreach ($name in @("ISCC.exe", "ISCC")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw @"
Inno Setup 7 ISCC.exe was not found.
Install the official 64-bit Inno Setup 7 compiler and rerun this command:
  winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
"@
}

if (-not $SkipAppBuild) {
    Remove-Item -LiteralPath $installerWorkDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $installerWorkDir | Out-Null
    & pwsh -NoLogo -NoProfile -NonInteractive `
        -File (Join-Path $repositoryRoot "scripts\build-windows-exe.ps1") `
        -DistDir $appDistDir `
        -WorkDir $appWorkDir
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 3 executable staging build failed with exit code $LASTEXITCODE"
    }
}
if (-not (Test-Path -LiteralPath $mainExe -PathType Leaf)) {
    throw "packaged codemcp-remote.exe was not found: $mainExe"
}

$prepareArgs = @(
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-File", (Join-Path $repositoryRoot "scripts\prepare-remote-transport.ps1"),
    "-DestinationDir", $appDir,
    "-CloudflaredVersion", $CloudflaredVersion,
    "-OpenAITunnelClientVersion", $TunnelClientVersion
)
if ($ForceTunnelDownload) {
    $prepareArgs += "-ForceDownload"
}
$transportJson = (& pwsh @prepareArgs | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "remote transport preparation failed with exit code $LASTEXITCODE"
}
try {
    $transportInfo = $transportJson | ConvertFrom-Json
} catch {
    throw "remote transport preparation did not return valid JSON: $transportJson"
}
if ($transportInfo.status -ne "ok" -or $transportInfo.recommended_provider -ne "cloudflare") {
    throw "remote transport preparation did not report the expected Cloudflare-ready payload"
}

$forbiddenPayloadPaths = @(
    (Join-Path $appDir "config\tunnel-profile.local.env"),
    (Join-Path $appDir "config\remote.toml"),
    (Join-Path $appDir "config\tunnel.env"),
    (Join-Path $appDir "run\state.json"),
    (Join-Path $appDir "data\bridge.sqlite3"),
    (Join-Path $appDir ".local\bridge.sqlite3"),
    (Join-Path $appDir "bridge.sqlite3")
)
$forbiddenPayload = @(
    $forbiddenPayloadPaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
)
$forbiddenPayload += @(
    Get-ChildItem -LiteralPath $appDir -Recurse -File -Filter "*.dpapi" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName }
)
if ($forbiddenPayload.Count -gt 0) {
    throw "installer payload contains runtime/secret files: $($forbiddenPayload -join ', ')"
}

$iscc = Resolve-ISCC -RequestedPath $ISCCPath
Remove-Item -LiteralPath $OutputDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$isccArgs = @(
    "/Qp",
    ("/DSourceDir={0}" -f $appDir),
    ("/DOutputDir={0}" -f $OutputDir),
    ("/DAppVersion={0}" -f $AppVersion),
    $installerScript
)
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compiler failed with exit code $LASTEXITCODE"
}

$setupPath = Join-Path $OutputDir "codemcp-remote-setup.exe"
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "expected installer was not created: $setupPath"
}

$setupSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $setupPath).Hash.ToLowerInvariant()
$checksumPath = Join-Path $OutputDir "SHA256SUMS.txt"
("{0}  codemcp-remote-setup.exe" -f $setupSha256) |
    Set-Content -LiteralPath $checksumPath -Encoding ascii

$signature = Get-AuthenticodeSignature -LiteralPath $setupPath
$signatureStatus = [string]$signature.Status
if ($signatureStatus -notin @("Valid", "NotSigned")) {
    throw "installer Authenticode status is unsafe: $signatureStatus"
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

$smokeStatus = "skipped"
$smokeMode = "skipped"
$smokeInstallerPath = $null
$productionInstallLocationBefore = $null
if (-not $SkipSmoke) {
    $smokeRoot = Join-Path $repositoryRoot ".local\installer-smoke"
    $installDir = Join-Path $smokeRoot "installed"
    $runtimeDir = Join-Path $smokeRoot "runtime"
    $uninstallKey = $productionUninstallKey
    $smokeInstallerPath = $setupPath

    if (Test-Path -LiteralPath $productionUninstallKey) {
        $smokeMode = "isolated-existing-production-install"
        $productionInstallLocationBefore = [string](
            Get-ItemProperty -LiteralPath $productionUninstallKey -ErrorAction Stop
        ).InstallLocation
        $uninstallKey = $smokeUninstallKey
        $smokeCompilerOutputDir = Join-Path $installerWorkDir "smoke-installer"
        Remove-Item -LiteralPath $smokeCompilerOutputDir -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $smokeCompilerOutputDir | Out-Null
        $smokeIsccArgs = @(
            "/Qp",
            ("/DSourceDir={0}" -f $appDir),
            ("/DOutputDir={0}" -f $smokeCompilerOutputDir),
            ("/DAppVersion={0}" -f $AppVersion),
            ("/DInstallerAppId={0}" -f ("{" + $smokeAppId)),
            "/DInstallerAppName=codemcp-remote Installer Smoke",
            "/DInstallerGroupName=codemcp-remote Installer Smoke",
            ("/DProductRegistryKey={0}" -f $smokeProductRegistryKey),
            $installerScript
        )
        & $iscc @smokeIsccArgs
        if ($LASTEXITCODE -ne 0) {
            throw "isolated Inno Setup smoke compiler failed with exit code $LASTEXITCODE"
        }
        $smokeInstallerPath = Join-Path $smokeCompilerOutputDir "codemcp-remote-setup.exe"
        if (-not (Test-Path -LiteralPath $smokeInstallerPath -PathType Leaf)) {
            throw "isolated installer smoke executable was not created: $smokeInstallerPath"
        }
        Remove-Item -LiteralPath $smokeProductRegistryPath -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        $smokeMode = "production-clean-host"
    }

    if (Test-Path -LiteralPath $uninstallKey) {
        $existingInstall = (Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction Stop).InstallLocation
        $existingFullPath = if ([string]::IsNullOrWhiteSpace($existingInstall)) {
            ""
        } else {
            [System.IO.Path]::GetFullPath($existingInstall)
        }
        $smokePrefix = [System.IO.Path]::GetFullPath($smokeRoot).TrimEnd("\") + "\"
        if (-not $existingFullPath.StartsWith($smokePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "installer smoke registration points outside the isolated smoke root: $existingFullPath"
        }
        Write-Host "Removing stale isolated installer smoke state: $existingFullPath"
        Remove-Item -LiteralPath $uninstallKey -Recurse -Force
        if (Test-Path -LiteralPath $existingFullPath) {
            Remove-Item -LiteralPath $existingFullPath -Recurse -Force
        }
        if (Test-Path -LiteralPath $uninstallKey) {
            throw "failed to remove stale installer smoke registration: $uninstallKey"
        }
    }

    Remove-Item -LiteralPath $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
    $setupLog = Join-Path $smokeRoot "setup.log"

    try {
        $setupExit = Invoke-GuiProcessAndWait `
            -FilePath $smokeInstallerPath `
            -ArgumentList @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOSTOPLIFECYCLE",
                ('/DIR="{0}"' -f $installDir),
                ('/LOG="{0}"' -f $setupLog),
                "/MERGETASKS=!addtopath"
            )
        if ($setupExit -ne 0) {
            throw "silent installer smoke failed with exit code $setupExit; log=$setupLog"
        }
        if (-not (Test-Path -LiteralPath $uninstallKey)) {
            throw "installer smoke did not create the expected uninstall registration; log=$setupLog"
        }

        $installedLocation = (Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction Stop).InstallLocation
        if ([string]::IsNullOrWhiteSpace($installedLocation)) {
            throw "installer smoke uninstall registration has no InstallLocation; log=$setupLog"
        }
        $installedLocation = [System.IO.Path]::GetFullPath($installedLocation)
        $expectedLocation = [System.IO.Path]::GetFullPath($installDir)
        if ($installedLocation.TrimEnd("\") -ne $expectedLocation.TrimEnd("\")) {
            $unexpectedUninstallers = @(
                Get-ChildItem -LiteralPath $installedLocation -File -Filter "unins*.exe" -ErrorAction SilentlyContinue
            )
            if ($unexpectedUninstallers.Count -eq 1) {
                $null = Invoke-GuiProcessAndWait `
                    -FilePath $unexpectedUninstallers[0].FullName `
                    -ArgumentList @(
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                        "/NOSTOPLIFECYCLE"
                    )
            }
            throw "installer smoke installed to unexpected location: $installedLocation; expected: $expectedLocation; log=$setupLog"
        }

        $installedMain = Join-Path $installedLocation "codemcp-remote.exe"
        $installedTunnel = Join-Path $installedLocation "tunnel-client.exe"
        $installedCloudflared = Join-Path $installedLocation "cloudflared.exe"
        $requiredFiles = @(
            $installedMain,
            $installedCloudflared,
            $installedTunnel,
            (Join-Path $installedLocation "codemcp-start.cmd"),
            (Join-Path $installedLocation "codemcp-stop.cmd"),
            (Join-Path $installedLocation "LICENSE"),
            (Join-Path $installedLocation "BUILD_PROVENANCE.json"),
            (Join-Path $installedLocation "THIRD_PARTY_NOTICES.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\pyinstaller\COPYING.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\pyinstaller\NOTICE.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\codemcp\LICENSE.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\codemcp\NOTICE.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\cloudflared\LICENSE"),
            (Join-Path $installedLocation "THIRD_PARTY\cloudflared\NOTICE.txt"),
            (Join-Path $installedLocation "THIRD_PARTY\tunnel-client\LICENSE")
        )
        foreach ($required in $requiredFiles) {
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
                throw "installer smoke missing required file: $required; log=$setupLog"
            }
        }

        & $installedMain --version
        if ($LASTEXITCODE -ne 0) {
            throw "installed codemcp-remote version smoke failed"
        }
        $installedCloudflaredVersion = (& $installedCloudflared --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or
            $installedCloudflaredVersion -notmatch [regex]::Escape($CloudflaredVersion)) {
            throw "installed cloudflared version smoke failed: $installedCloudflaredVersion"
        }
        $installedCloudflaredSha256 = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $installedCloudflared
        ).Hash.ToLowerInvariant()
        if ($installedCloudflaredSha256 -ne [string]$transportInfo.cloudflare.executable_sha256) {
            throw "installed cloudflared checksum differs from the verified staging payload"
        }
        $installedTunnelVersion = (& $installedTunnel --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or
            $installedTunnelVersion -notmatch [regex]::Escape($TunnelClientVersion.TrimStart("v"))) {
            throw "installed tunnel-client version smoke failed: $installedTunnelVersion"
        }

        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        $runtimeSentinel = Join-Path $runtimeDir "preserve-on-upgrade-and-uninstall.txt"
        "phase-5.5.5-runtime-preservation" |
            Set-Content -LiteralPath $runtimeSentinel -Encoding ascii
        & $installedMain status --home $runtimeDir
        if ($LASTEXITCODE -ne 0) {
            throw "installed lifecycle status smoke failed"
        }

        $upgradeExit = Invoke-GuiProcessAndWait `
            -FilePath $smokeInstallerPath `
            -ArgumentList @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOSTOPLIFECYCLE",
                ('/DIR="{0}"' -f $installDir),
                "/MERGETASKS=!addtopath"
            )
        if ($upgradeExit -ne 0) {
            throw "silent installer upgrade smoke failed with exit code $upgradeExit"
        }
        if (-not (Test-Path -LiteralPath $runtimeSentinel -PathType Leaf)) {
            throw "installer upgrade removed user runtime data"
        }

        $uninstallers = @(
            Get-ChildItem -LiteralPath $installedLocation -File -Filter "unins*.exe"
        )
        if ($uninstallers.Count -ne 1) {
            throw "expected exactly one Inno Setup uninstaller"
        }
        $uninstallExit = Invoke-GuiProcessAndWait `
            -FilePath $uninstallers[0].FullName `
            -ArgumentList @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOSTOPLIFECYCLE"
            )
        if ($uninstallExit -ne 0) {
            throw "silent uninstaller smoke failed with exit code $uninstallExit"
        }
        if (Test-Path -LiteralPath $installedMain -PathType Leaf) {
            throw "silent uninstall left codemcp-remote.exe behind"
        }
        if (-not (Test-Path -LiteralPath $runtimeSentinel -PathType Leaf)) {
            throw "silent uninstall removed user runtime data"
        }
        if (Test-Path -LiteralPath $uninstallKey) {
            throw "silent uninstall left the smoke uninstall registration behind: $uninstallKey"
        }
        if ($smokeMode -eq "isolated-existing-production-install") {
            if (-not (Test-Path -LiteralPath $productionUninstallKey)) {
                throw "isolated installer smoke removed the production uninstall registration"
            }
            $productionInstallLocationAfter = [string](
                Get-ItemProperty -LiteralPath $productionUninstallKey -ErrorAction Stop
            ).InstallLocation
            if ($productionInstallLocationAfter -ne $productionInstallLocationBefore) {
                throw "isolated installer smoke changed the production InstallLocation"
            }
        }
        $smokeStatus = "passed"
    } finally {
        if ($smokeMode -eq "isolated-existing-production-install") {
            Remove-Item -LiteralPath $smokeProductRegistryPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($smokeStatus -eq "passed") {
            Remove-Item -LiteralPath $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "Installer smoke artifacts were preserved for diagnosis: $smokeRoot"
        }
    }
}

[ordered]@{
    status = "ok"
    phase = "5.5.5"
    app_version = $AppVersion
    installer_builder = "Inno Setup 7"
    iscc = $iscc
    installer = $setupPath
    staging_payload = $appDir
    sha256 = $setupSha256
    sha256_file = $checksumPath
    authenticode_status = $signatureStatus
    recommended_transport = [string]$transportInfo.recommended_provider
    cloudflared_version = [string]$transportInfo.cloudflare.version
    cloudflared_sha256 = [string]$transportInfo.cloudflare.executable_sha256
    cloudflared_license = [string]$transportInfo.cloudflare.license
    tunnel_client_version = [string]$transportInfo.openai_tunnel.version
    tunnel_client_sha256 = [string]$transportInfo.openai_tunnel.executable_sha256
    tunnel_client_license = [string]$transportInfo.openai_tunnel.license
    smoke = $smokeStatus
    smoke_mode = $smokeMode
} | ConvertTo-Json -Depth 4
