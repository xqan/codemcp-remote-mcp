[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$ProfileName,
    [string]$ProfileDir,
    [string]$BridgeUrl,
    [string]$TunnelHealthUrl,
    [string]$BridgeConfig,
    [string]$ProjectsConfig,
    [switch]$SkipTunnel
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "tunnel-common.ps1")

$repositoryRoot = Get-Phase5RepositoryRoot
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $repositoryRoot "config\tunnel-profile.local.env"
} else {
    $EnvFile = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $EnvFile
}
$settings = Get-Phase5Settings `
    -RepositoryRoot $repositoryRoot `
    -EnvFile $EnvFile `
    -ProfileName $ProfileName `
    -ProfileDir $ProfileDir `
    -BridgeUrl $BridgeUrl `
    -TunnelHealthUrl $TunnelHealthUrl

if ([string]::IsNullOrWhiteSpace($BridgeConfig)) {
    $BridgeConfig = Join-Path $repositoryRoot "config\bridge.example.toml"
} else {
    $BridgeConfig = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $BridgeConfig
}
if ([string]::IsNullOrWhiteSpace($ProjectsConfig)) {
    $ProjectsConfig = Join-Path $repositoryRoot "config\projects.toml"
    if (-not (Test-Path -LiteralPath $ProjectsConfig -PathType Leaf)) {
        $ProjectsConfig = Join-Path $repositoryRoot "config\projects.example.toml"
    }
} else {
    $ProjectsConfig = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $ProjectsConfig
}

function Get-Phase6TomlString {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Section,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [AllowNull()]
        [string]$Default
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $Default
    }
    $currentSection = ""
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[(?<section>[^\]]+)\]\s*$') {
            $currentSection = $Matches["section"]
            continue
        }
        if ($currentSection -ne $Section -or
            $trimmed -notmatch ("^(?<key>" + [regex]::Escape($Key) + ")\s*=\s*(?<value>.+)$")) {
            continue
        }
        $value = ($Matches["value"] -split '\s+#', 2)[0].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            return $value.Substring(1, $value.Length - 2)
        }
        return $value
    }
    return $Default
}

function ConvertTo-Phase6WslPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($fullPath -match '^(?<drive>[A-Za-z]):\\(?<tail>.*)$') {
        return "/mnt/{0}/{1}" -f $Matches["drive"].ToLowerInvariant(), ($Matches["tail"] -replace '\\', '/')
    }
    return $fullPath -replace '\\', '/'
}

function New-Phase6PathCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$Leaf
    )

    $exists = if ($Leaf) {
        Test-Path -LiteralPath $Path -PathType Leaf
    } else {
        Test-Path -LiteralPath $Path -PathType Container
    }
    return [ordered]@{
        path = $Path
        status = if ($exists) { "ok" } else { "missing" }
    }
}

$dataDirValue = Get-Phase6TomlString -Path $BridgeConfig -Section "storage" -Key "data_dir" -Default ".local"
$sqliteFileValue = Get-Phase6TomlString -Path $BridgeConfig -Section "storage" -Key "sqlite_file" -Default ".local/bridge.sqlite3"
$logDirValue = Get-Phase6TomlString -Path $BridgeConfig -Section "storage" -Key "log_dir" -Default ".local/logs"
$workerMode = Get-Phase6TomlString -Path $BridgeConfig -Section "codemcp" -Key "worker_mode" -Default "local"
$wslDistribution = Get-Phase6TomlString -Path $BridgeConfig -Section "codemcp" -Key "wsl_distribution" -Default "Ubuntu"
$wslPythonConfig = Get-Phase6TomlString -Path $BridgeConfig -Section "codemcp" -Key "wsl_python" -Default ""
$dataDirPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $dataDirValue
$sqliteFilePath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $sqliteFileValue
$logDirPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $logDirValue
$workerVenvPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value ".local/bridge-venv-wsl"
$workerPythonWindowsPath = Join-Path $workerVenvPath "bin\python"
$profilePath = Get-Phase5ProfilePath -ProfileDir $settings.ProfileDir -ProfileName $settings.ProfileName

$report = [ordered]@{
    phase = "6"
    repository_root = $repositoryRoot
    paths = [ordered]@{}
    database = [ordered]@{}
    worker = [ordered]@{}
    git = [ordered]@{}
    bridge = [ordered]@{}
    tunnel = [ordered]@{}
}
$checksPassed = $true
$phase6ChecksPassed = $true

$report.paths.repository_root = New-Phase6PathCheck -Path $repositoryRoot
$report.paths.bridge_config = New-Phase6PathCheck -Path $BridgeConfig -Leaf
$report.paths.projects_config = New-Phase6PathCheck -Path $ProjectsConfig -Leaf
$report.paths.data_dir = New-Phase6PathCheck -Path $dataDirPath
$report.paths.log_dir = if (Test-Path -LiteralPath $logDirPath -PathType Container) {
    New-Phase6PathCheck -Path $logDirPath
} else {
    [ordered]@{ path = $logDirPath; status = "not_initialized" }
}
$report.paths.worker_venv = if ($workerMode -eq "wsl2") {
    New-Phase6PathCheck -Path $workerVenvPath
} else {
    [ordered]@{ path = $workerVenvPath; status = "not_required" }
}
$report.paths.env_file = if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
    New-Phase6PathCheck -Path $EnvFile -Leaf
} else {
    [ordered]@{ path = $EnvFile; status = "not_configured" }
}
$report.paths.tunnel_profile = if ($SkipTunnel) {
    [ordered]@{ path = $profilePath; status = "skipped" }
} elseif (Test-Path -LiteralPath $profilePath -PathType Leaf) {
    New-Phase6PathCheck -Path $profilePath -Leaf
} else {
    [ordered]@{ path = $profilePath; status = "missing" }
}
foreach ($requiredPathName in @("bridge_config", "projects_config", "data_dir")) {
    if ($report.paths[$requiredPathName].status -eq "missing") {
        $phase6ChecksPassed = $false
    }
}

$databaseParent = Split-Path -Parent $sqliteFilePath
$databaseExists = Test-Path -LiteralPath $sqliteFilePath -PathType Leaf
$databaseParentExists = Test-Path -LiteralPath $databaseParent -PathType Container
$report.database = [ordered]@{
    path = $sqliteFilePath
    parent = $databaseParent
    status = if (-not $databaseParentExists) {
        "missing_parent"
    } elseif ($databaseExists) {
        "ready"
    } else {
        "not_initialized"
    }
    size_bytes = if ($databaseExists) { (Get-Item -LiteralPath $sqliteFilePath).Length } else { $null }
    wal_file = Test-Path -LiteralPath ("{0}-wal" -f $sqliteFilePath) -PathType Leaf
    shm_file = Test-Path -LiteralPath ("{0}-shm" -f $sqliteFilePath) -PathType Leaf
}
if (-not $databaseParentExists) {
    $phase6ChecksPassed = $false
}

$report.worker = [ordered]@{
    mode = $workerMode
    distribution = $wslDistribution
    configured_python = if ([string]::IsNullOrWhiteSpace($wslPythonConfig)) { $null } else { $wslPythonConfig }
    venv_path = $workerVenvPath
    python_path = $null
    status = "not_checked"
}
if ($workerMode -eq "wsl2" -and $env:OS -eq "Windows_NT") {
    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($null -eq $wslCommand) {
        $report.worker.status = "missing_dependency"
        $phase6ChecksPassed = $false
    } else {
        $wslDistributionOutput = (& $wslCommand.Source -l -q 2>&1 | Out-String).Trim() -replace "`0", ""
        $wslDistributionExit = $LASTEXITCODE
        $availableDistributions = @(
            $wslDistributionOutput -split "`r?`n" |
                ForEach-Object { $_.Trim() } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        $distributionAvailable = $wslDistributionExit -eq 0 -and $availableDistributions -contains $wslDistribution
        $report.worker.distribution_available = $distributionAvailable
        $report.worker.available_distributions = $availableDistributions
        if (-not $distributionAvailable) {
            $report.worker.status = "distribution_missing"
            $phase6ChecksPassed = $false
        } else {
            $wslPythonPath = if ([string]::IsNullOrWhiteSpace($wslPythonConfig)) {
                ConvertTo-Phase6WslPath -Path $workerPythonWindowsPath
            } else {
                $wslPythonConfig
            }
            $report.worker.python_path = $wslPythonPath
            $wslPythonRawOutput = (& $wslCommand.Source --distribution $wslDistribution -- $wslPythonPath --version 2>&1 | Out-String)
            $wslPythonExit = $LASTEXITCODE
            $wslPythonOutput = $wslPythonRawOutput -replace "`0", ""
            $pythonVersionLines = @(
                $wslPythonOutput -split "`r?`n" |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ -match "^(?i:Python\s+)" }
            )
            $report.worker.python_version = if ($pythonVersionLines.Count -gt 0) {
                $pythonVersionLines[-1]
            } else {
                $wslPythonOutput.Trim()
            }
            if ($wslPythonExit -eq 0) {
                $report.worker.status = "ok"
            } else {
                $report.worker.status = "python_unavailable"
                $phase6ChecksPassed = $false
            }
        }
    }
} elseif ($workerMode -eq "local") {
    $report.worker.status = "local_worker"
} else {
    $report.worker.status = "not_checked"
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    $report.git = [ordered]@{ status = "missing_dependency"; repository_root = $repositoryRoot }
    $phase6ChecksPassed = $false
} else {
    $gitRootOutput = (& $gitCommand.Source -C $repositoryRoot rev-parse --show-toplevel 2>&1 | Out-String).Trim()
    $gitRootExit = $LASTEXITCODE
    if ($gitRootExit -ne 0) {
        $report.git = [ordered]@{
            status = "not_repository"
            repository_root = $repositoryRoot
            error = $gitRootOutput
        }
        $phase6ChecksPassed = $false
    } else {
        $gitStatusOutput = (& $gitCommand.Source -C $repositoryRoot status --porcelain 2>&1 | Out-String).Trim()
        $gitStatusExit = $LASTEXITCODE
        $dirtyCount = if ([string]::IsNullOrWhiteSpace($gitStatusOutput)) {
            0
        } else {
            @($gitStatusOutput -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
        }
        $report.git = [ordered]@{
            status = if ($gitStatusExit -eq 0) {
                if ($dirtyCount -eq 0) { "clean" } else { "dirty" }
            } else {
                "status_failed"
            }
            repository_root = $gitRootOutput
            dirty_count = $dirtyCount
        }
        if ($gitStatusExit -ne 0) {
            $phase6ChecksPassed = $false
        }
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    $report.bridge.doctor = @{ status = "missing_dependency"; error = "uv was not found on PATH" }
    $report.bridge.healthz = @{ status = "not_checked" }
    $checksPassed = $false
} else {
    $bridgeConfigurationOutput = (& $uv.Source run --project (Join-Path $repositoryRoot "bridge") `
        codemcp-bridge-server check `
        --bridge-config $BridgeConfig `
        --projects-config $ProjectsConfig 2>&1 | Out-String).Trim()
    $bridgeConfigurationExit = $LASTEXITCODE
    $report.bridge.configuration = [ordered]@{
        status = if ($bridgeConfigurationExit -eq 0) { "ok" } else { "failed" }
        exit_code = $bridgeConfigurationExit
        output = Protect-Phase5DiagnosticText -Text $bridgeConfigurationOutput
    }
    if ($bridgeConfigurationExit -ne 0) {
        $phase6ChecksPassed = $false
    }

    $bridgeDoctorOutput = (& $uv.Source run --project (Join-Path $repositoryRoot "bridge") `
        codemcp-bridge doctor --strict --json 2>&1 | Out-String).Trim()
    $bridgeDoctorExit = $LASTEXITCODE
    $report.bridge.doctor = [ordered]@{
        status = if ($bridgeDoctorExit -eq 0) { "ok" } else { "failed" }
        exit_code = $bridgeDoctorExit
        output = Protect-Phase5DiagnosticText -Text $bridgeDoctorOutput
    }
    if ($bridgeDoctorExit -ne 0) {
        $checksPassed = $false
    }

    $bridgeUri = [Uri]$settings.BridgeUrl
    $bridgeHealthUrl = "{0}://{1}/healthz" -f $bridgeUri.Scheme, $bridgeUri.Authority
    $bridgeHealth = Test-Phase5HttpEndpoint -Url $bridgeHealthUrl
    $report.bridge.healthz = $bridgeHealth
    if ($bridgeHealth.status -ne "ok") {
        $checksPassed = $false
    }
}

$checksPassed = $checksPassed -and $phase6ChecksPassed

if ($SkipTunnel) {
    $report.tunnel = @{ status = "skipped"; reason = "-SkipTunnel was supplied" }
} else {
    $tunnelClient = Get-Command tunnel-client -ErrorAction SilentlyContinue
    if ($null -eq $tunnelClient) {
        $report.tunnel = @{ status = "missing_dependency"; error = "tunnel-client was not found on PATH" }
        $checksPassed = $false
    } elseif (-not $settings.ApiKeyPresent -or [string]::IsNullOrWhiteSpace($settings.TunnelId)) {
        $report.tunnel = [ordered]@{
            status = "not_configured"
            error = "CONTROL_PLANE_TUNNEL_ID and CONTROL_PLANE_API_KEY are required"
        }
        $checksPassed = $false
    } else {
        try {
            Assert-Phase5TunnelId -TunnelId $settings.TunnelId
            $profilePath = Get-Phase5ProfilePath `
                -ProfileDir $settings.ProfileDir `
                -ProfileName $settings.ProfileName
            Assert-Phase5ProfileContract `
                -ProfilePath $profilePath `
                -TunnelId $settings.TunnelId `
                -BridgeUrl $settings.BridgeUrl

            $tunnelDoctorOutput = (& $tunnelClient.Source doctor `
                --profile $settings.ProfileName `
                --profile-dir $settings.ProfileDir `
                --health.listen-addr "127.0.0.1:0" `
                --explain --json 2>&1 | Out-String).Trim()
            $tunnelDoctorExit = $LASTEXITCODE
            $tunnelHealth = Test-Phase5HttpEndpoint -Url ("{0}/healthz" -f $settings.TunnelHealthUrl)
            $tunnelReady = Test-Phase5HttpEndpoint -Url ("{0}/readyz" -f $settings.TunnelHealthUrl)
            $report.tunnel = [ordered]@{
                status = if ($tunnelDoctorExit -eq 0 -and
                    $tunnelHealth.status -eq "ok" -and
                    $tunnelReady.status -eq "ok") { "ok" } else { "failed" }
                profile = $settings.ProfileName
                profile_path = $profilePath
                doctor = [ordered]@{
                    exit_code = $tunnelDoctorExit
                    output = Protect-Phase5DiagnosticText -Text $tunnelDoctorOutput
                }
                healthz = $tunnelHealth
                readyz = $tunnelReady
            }
            if ($report.tunnel.status -ne "ok") {
                $checksPassed = $false
            }
        } catch {
            $report.tunnel = [ordered]@{
                status = "invalid_configuration"
                error = $_.Exception.Message
            }
            $checksPassed = $false
        }
    }
}

$report.status = if ($checksPassed) { "ok" } else { "failed" }
$report | ConvertTo-Json -Depth 8
if (-not $checksPassed) {
    exit 1
}
exit 0
