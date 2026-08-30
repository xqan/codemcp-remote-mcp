[CmdletBinding()]
param(
    [string]$BridgeConfig,
    [string]$ProjectsConfig,
    [string]$EnvFile,
    [string]$ProfileName,
    [string]$ProfileDir,
    [string]$BridgeUrl,
    [string]$TunnelHealthUrl,
    [string]$LogDir,
    [string]$HealthListenAddress,
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSec = 45,
    [switch]$Initialize,
    [switch]$Force
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

$effectiveHealthListenAddress = $HealthListenAddress
if ([string]::IsNullOrWhiteSpace($effectiveHealthListenAddress)) {
    $effectiveHealthListenAddress = $settings.HealthListenAddress
}
$effectiveHealthListenAddress = Assert-Phase5HealthListenAddress -Value $effectiveHealthListenAddress
if (-not [string]::IsNullOrWhiteSpace($TunnelHealthUrl)) {
    $env:TUNNEL_HEALTH_URL = $settings.TunnelHealthUrl
}

$bridgeConfigValue = $BridgeConfig
if ([string]::IsNullOrWhiteSpace($bridgeConfigValue)) {
    $bridgeConfigValue = "config\bridge.example.toml"
}
$bridgeConfigPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $bridgeConfigValue
if (-not (Test-Path -LiteralPath $bridgeConfigPath -PathType Leaf)) {
    throw "Bridge config was not found: $bridgeConfigPath"
}

$projectsConfigValue = $ProjectsConfig
if ([string]::IsNullOrWhiteSpace($projectsConfigValue)) {
    $projectsConfigValue = Join-Path $repositoryRoot "config\projects.toml"
    if (-not (Test-Path -LiteralPath $projectsConfigValue -PathType Leaf)) {
        $projectsConfigValue = Join-Path $repositoryRoot "config\projects.example.toml"
    }
}
$projectsConfigPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $projectsConfigValue
if (-not (Test-Path -LiteralPath $projectsConfigPath -PathType Leaf)) {
    throw "Projects config was not found: $projectsConfigPath"
}

$logDirValue = $LogDir
if ([string]::IsNullOrWhiteSpace($logDirValue)) {
    $logDirValue = Get-Phase5TomlString `
        -Path $bridgeConfigPath `
        -Section "storage" `
        -Key "log_dir" `
        -Default ".local/logs"
}
$logDirPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $logDirValue

$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($null -eq $pwsh) {
    throw "pwsh was not found on PATH"
}

$bridgeScript = Join-Path $repositoryRoot "scripts\start-bridge.ps1"
$tunnelScript = Join-Path $repositoryRoot "scripts\start-tunnel.ps1"
$bridgeProject = Join-Path $repositoryRoot "bridge"
$repositoryPattern = [regex]::Escape($repositoryRoot.TrimEnd("\", "/"))
$bridgeProjectPattern = [regex]::Escape($bridgeProject.TrimEnd("\", "/"))
$profileDirPattern = [regex]::Escape($settings.ProfileDir.TrimEnd("\", "/"))
$startedProcesses = @()
$processSnapshot = $null
$services = [ordered]@{
    bridge = [ordered]@{}
    tunnel = [ordered]@{}
}

function Start-Phase6BackgroundScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $pwsh.Source
    $startInfo.WorkingDirectory = $repositoryRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    [void]$startInfo.ArgumentList.Add("-NoLogo")
    [void]$startInfo.ArgumentList.Add("-NoProfile")
    [void]$startInfo.ArgumentList.Add("-NonInteractive")
    [void]$startInfo.ArgumentList.Add("-File")
    [void]$startInfo.ArgumentList.Add($ScriptPath)
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "failed to start $ScriptPath"
    }
    return $process
}

function Wait-Phase6Endpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    $lastCheck = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            return [pscustomobject]@{
                status = "failed"
                url = $Url
                error = "startup process exited before the endpoint became healthy"
                exit_code = $Process.ExitCode
                last_check = $lastCheck
            }
        }

        $lastCheck = Test-Phase5HttpEndpoint -Url $Url
        if ($lastCheck.status -eq "ok") {
            return [pscustomobject]@{
                status = "ok"
                url = $Url
                status_code = $lastCheck.status_code
            }
        }
        Start-Sleep -Milliseconds 500
    }

    return [pscustomobject]@{
        status = "timeout"
        url = $Url
        error = "endpoint did not become healthy within $TimeoutSec seconds"
        last_check = $lastCheck
    }
}

function Stop-Phase6StartedTree {
    param(
        [AllowNull()]
        [System.Diagnostics.Process]$Process
    )

    if ($null -eq $Process) {
        return
    }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
        }
    } catch {
        Write-Warning ("cleanup failed for PID {0}: {1}" -f $Process.Id, $_.Exception.Message)
    }
}

function Get-Phase6ProcessSnapshot {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object ProcessId, Name, CommandLine)
    } catch {
        throw "Unable to verify existing services; run start-all.ps1 with permission to query Win32_Process"
    }
}

function Test-Phase6OwnedService {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Snapshot,
        [Parameter(Mandatory = $true)]
        [ValidateSet("bridge", "tunnel")]
        [string]$Category
    )

    foreach ($process in @($Snapshot)) {
        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }
        if ($Category -eq "bridge") {
            $isBridge = (
                $commandLine -match "(?i)codemcp-bridge-server" -and
                $commandLine -match $repositoryPattern
            ) -or (
                $commandLine -match "(?i)\buv(?:\.exe)?\b.*\brun\b" -and
                $commandLine -match $bridgeProjectPattern
            ) -or (
                $commandLine -match "(?i)start-bridge\.ps1" -and
                $commandLine -match $repositoryPattern
            )
            if ($isBridge) {
                return $true
            }
        } else {
            $isTunnel = (
                ($process.Name -ieq "tunnel-client.exe" -or
                    $commandLine -match "(?i)(^|[\\/])tunnel-client(?:\.exe)?") -and
                $commandLine -match $profileDirPattern
            ) -or (
                $commandLine -match "(?i)start-tunnel\.ps1" -and
                $commandLine -match $profileDirPattern
            )
            if ($isTunnel) {
                return $true
            }
        }
    }
    return $false
}

$bridgeHealthUri = [Uri]$settings.BridgeUrl
$bridgeHealthUrl = "{0}://{1}/healthz" -f $bridgeHealthUri.Scheme, $bridgeHealthUri.Authority
$tunnelReadyUrl = "{0}/readyz" -f $settings.TunnelHealthUrl.TrimEnd("/")

try {
    $bridgeHealth = Test-Phase5HttpEndpoint -Url $bridgeHealthUrl
    if ($bridgeHealth.status -eq "ok") {
        $processSnapshot = Get-Phase6ProcessSnapshot
        if (-not (Test-Phase6OwnedService -Snapshot $processSnapshot -Category "bridge")) {
            throw "Bridge health endpoint is already in use by an unrecognized process"
        }
        $services.bridge = [ordered]@{
            status = "already_running"
            health = $bridgeHealth
        }
    } else {
        $bridgeArguments = [System.Collections.Generic.List[string]]::new()
        [void]$bridgeArguments.Add("-BridgeConfig")
        [void]$bridgeArguments.Add($bridgeConfigPath)
        [void]$bridgeArguments.Add("-ProjectsConfig")
        [void]$bridgeArguments.Add($projectsConfigPath)
        $bridgeProcess = Start-Phase6BackgroundScript -ScriptPath $bridgeScript -Arguments $bridgeArguments.ToArray()
        $startedProcesses += $bridgeProcess
        $bridgeWait = Wait-Phase6Endpoint `
            -Url $bridgeHealthUrl `
            -Process $bridgeProcess `
            -TimeoutSec $StartupTimeoutSec
        if ($bridgeWait.status -ne "ok") {
            throw ("Bridge startup failed: {0}" -f ($bridgeWait | ConvertTo-Json -Depth 8 -Compress))
        }
        $services.bridge = [ordered]@{
            status = "started"
            process_id = $bridgeProcess.Id
            health = $bridgeWait
        }
    }

    $tunnelReady = Test-Phase5HttpEndpoint -Url $tunnelReadyUrl
    if ($tunnelReady.status -eq "ok") {
        if ($null -eq $processSnapshot) {
            $processSnapshot = Get-Phase6ProcessSnapshot
        }
        if (-not (Test-Phase6OwnedService -Snapshot $processSnapshot -Category "tunnel")) {
            throw "Tunnel health endpoint is already in use by an unrecognized process"
        }
        $services.tunnel = [ordered]@{
            status = "already_running"
            health = $tunnelReady
        }
    } else {
        $tunnelArguments = [System.Collections.Generic.List[string]]::new()
        [void]$tunnelArguments.Add("-EnvFile")
        [void]$tunnelArguments.Add($EnvFile)
        [void]$tunnelArguments.Add("-ProfileName")
        [void]$tunnelArguments.Add($settings.ProfileName)
        [void]$tunnelArguments.Add("-ProfileDir")
        [void]$tunnelArguments.Add($settings.ProfileDir)
        [void]$tunnelArguments.Add("-BridgeUrl")
        [void]$tunnelArguments.Add($settings.BridgeUrl)
        [void]$tunnelArguments.Add("-LogDir")
        [void]$tunnelArguments.Add($logDirPath)
        [void]$tunnelArguments.Add("-HealthListenAddress")
        [void]$tunnelArguments.Add($effectiveHealthListenAddress)
        if ($Initialize) {
            [void]$tunnelArguments.Add("-Initialize")
        }
        if ($Force) {
            [void]$tunnelArguments.Add("-Force")
        }

        $tunnelProcess = Start-Phase6BackgroundScript -ScriptPath $tunnelScript -Arguments $tunnelArguments.ToArray()
        $startedProcesses += $tunnelProcess
        $tunnelWait = Wait-Phase6Endpoint `
            -Url $tunnelReadyUrl `
            -Process $tunnelProcess `
            -TimeoutSec $StartupTimeoutSec
        if ($tunnelWait.status -ne "ok") {
            throw ("Tunnel startup failed: {0}" -f ($tunnelWait | ConvertTo-Json -Depth 8 -Compress))
        }
        $services.tunnel = [ordered]@{
            status = "started"
            process_id = $tunnelProcess.Id
            health = $tunnelWait
        }
    }

    [ordered]@{
        phase = "6"
        status = "ok"
        repository_root = $repositoryRoot
        startup_timeout_sec = $StartupTimeoutSec
        services = $services
        note = "codemcp worker starts on demand inside the Bridge"
    } | ConvertTo-Json -Depth 8
    exit 0
} catch {
    foreach ($startedProcess in $startedProcesses) {
        Stop-Phase6StartedTree -Process $startedProcess
    }
    [ordered]@{
        phase = "6"
        status = "failed"
        repository_root = $repositoryRoot
        services = $services
        error = $_.Exception.Message
        cleanup = "attempted"
    } | ConvertTo-Json -Depth 8
    exit 1
}
