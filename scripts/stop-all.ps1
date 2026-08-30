[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [string]$EnvFile,
    [string]$ProfileName,
    [string]$ProfileDir,
    [string]$BridgeUrl
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "tunnel-common.ps1")

$repositoryRoot = Get-Phase5RepositoryRoot
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $repositoryRoot "config\tunnel-profile.local.env"
}
$settings = Get-Phase5Settings `
    -RepositoryRoot $repositoryRoot `
    -EnvFile $EnvFile `
    -ProfileName $ProfileName `
    -ProfileDir $ProfileDir `
    -BridgeUrl $BridgeUrl

$bridgeProject = Join-Path $repositoryRoot "bridge"
$repositoryPattern = [regex]::Escape($repositoryRoot.TrimEnd("\", "/"))
$bridgeProjectPattern = [regex]::Escape($bridgeProject.TrimEnd("\", "/"))
$profileDirPattern = [regex]::Escape($settings.ProfileDir.TrimEnd("\", "/"))

function Get-Phase6ProcessSnapshot {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object `
            ProcessId, Name, CommandLine, ParentProcessId, ExecutablePath)
    } catch {
        throw "Unable to inspect process command lines; run stop-all.ps1 with permission to query Win32_Process"
    }
}

function Get-Phase6Targets {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Snapshot
    )

    $rawTargets = @()
    foreach ($process in @($Snapshot)) {
        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

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
            $rawTargets += [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                Name = [string]$process.Name
                ParentProcessId = [int]$process.ParentProcessId
                Category = "bridge"
            }
        }

        $isTunnel = (
            ($process.Name -ieq "tunnel-client.exe" -or
                $commandLine -match "(?i)(^|[\\/])tunnel-client(?:\.exe)?") -and
            $commandLine -match $profileDirPattern
        )
        if ($isTunnel) {
            $rawTargets += [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                Name = [string]$process.Name
                ParentProcessId = [int]$process.ParentProcessId
                Category = "tunnel"
            }
        }

        $isWorker = (
            $process.Name -ieq "wsl.exe" -and
            $commandLine -match "(?i)(-m\s+codemcp\b|\bcodemcp\b)" -and
            ($commandLine -match "(?i)bridge-venv-wsl" -or
                $commandLine -match $repositoryPattern)
        ) -or (
            $commandLine -match "(?i)-m\s+codemcp_bridge\.native_codemcp_worker\b" -and
            ($commandLine -match $bridgeProjectPattern -or
                $commandLine -match $repositoryPattern)
        )
        if ($isWorker) {
            $rawTargets += [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                Name = [string]$process.Name
                ParentProcessId = [int]$process.ParentProcessId
                Category = "worker"
            }
        }
    }

    return @(
        $rawTargets |
            Group-Object ProcessId |
            ForEach-Object {
                $first = $_.Group[0]
                [pscustomobject]@{
                    ProcessId = $first.ProcessId
                    Name = $first.Name
                    ParentProcessId = $first.ParentProcessId
                    Categories = @($_.Group | ForEach-Object { $_.Category } | Sort-Object -Unique)
                }
            }
    )
}

function Get-Phase6Listeners {
    try {
        return @(
            Get-NetTCPConnection -State Listen -ErrorAction Stop |
                Where-Object {
                    $_.LocalAddress -in @("127.0.0.1", "::1") -and
                    $_.LocalPort -in @(46200, 46201)
                } |
                Select-Object LocalAddress, LocalPort, OwningProcess
        )
    } catch {
        return @()
    }
}

$snapshot = Get-Phase6ProcessSnapshot
$targets = @(Get-Phase6Targets -Snapshot $snapshot)
$targetById = @{}
$processById = @{}
foreach ($process in $snapshot) {
    $processById[[int]$process.ProcessId] = $process
}
foreach ($target in $targets) {
    $targetById[$target.ProcessId] = $target
}

$rootsById = @{}
foreach ($target in $targets) {
    $root = $target
    $parentId = $target.ParentProcessId
    while ($processById.ContainsKey($parentId)) {
        if ($targetById.ContainsKey($parentId)) {
            $root = $targetById[$parentId]
        }
        $parentId = [int]$processById[$parentId].ParentProcessId
    }
    $rootsById[$root.ProcessId] = $root
}

$actions = @()
foreach ($root in $rootsById.Values | Sort-Object ProcessId) {
    $description = "{0} PID {1} ({2})" -f $root.Name, $root.ProcessId, ($root.Categories -join ",")
    if ($PSCmdlet.ShouldProcess($description, "stop process tree")) {
        $taskkillOutput = (& taskkill.exe /PID $root.ProcessId /T /F 2>&1 | Out-String).Trim()
        $exitCode = $LASTEXITCODE
        $actions += [ordered]@{
            process_id = $root.ProcessId
            name = $root.Name
            categories = $root.Categories
            action = "stop"
            status = if ($exitCode -in @(0, 128)) { "ok" } else { "failed" }
            detail = if ($exitCode -in @(0, 128)) { $null } else { $taskkillOutput }
        }
        if ($exitCode -notin @(0, 128)) {
            throw "failed to stop $description"
        }
    } else {
        $actions += [ordered]@{
            process_id = $root.ProcessId
            name = $root.Name
            categories = $root.Categories
            action = "stop"
            status = "planned"
            detail = $null
        }
    }
}

$remaining = @()
$unrecognizedListeners = @()
if (-not $WhatIfPreference) {
    Start-Sleep -Milliseconds 300
    $remaining = @(Get-Phase6Targets -Snapshot (Get-Phase6ProcessSnapshot))
    $remainingIds = @($remaining | ForEach-Object { [int]$_.ProcessId })
    foreach ($listener in Get-Phase6Listeners) {
        if ($listener.OwningProcess -in $remainingIds) {
            continue
        }
        $unrecognizedListeners += [ordered]@{
            local_address = $listener.LocalAddress
            local_port = $listener.LocalPort
            owning_process = $listener.OwningProcess
        }
    }
}

$status = if ($WhatIfPreference) {
    "dry_run"
} elseif ($remaining.Count -eq 0 -and $unrecognizedListeners.Count -eq 0) {
    "ok"
} else {
    "incomplete"
}

[ordered]@{
    phase = "6"
    repository_root = $repositoryRoot
    profile = $settings.ProfileName
    profile_dir = $settings.ProfileDir
    status = $status
    actions = $actions
    remaining = @($remaining | ForEach-Object {
        [ordered]@{
            process_id = $_.ProcessId
            name = $_.Name
            categories = $_.Categories
        }
    })
    unrecognized_listeners = $unrecognizedListeners
} | ConvertTo-Json -Depth 8

if ($status -eq "incomplete") {
    exit 1
}
exit 0
