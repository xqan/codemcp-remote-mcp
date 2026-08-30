[CmdletBinding()]
param(
    [ValidateRange(1, 100)]
    [int]$Iterations = 20,
    [string]$RuntimeHome,
    [string]$InstallDir,
    [ValidateRange(1, 65535)]
    [int]$BridgePort = 46200,
    [ValidateRange(1, 65535)]
    [int]$MetricsPort = 46202
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Phase 6 packaged-runtime validation must run on Windows"
}
if ([string]::IsNullOrWhiteSpace($RuntimeHome)) {
    $RuntimeHome = Join-Path $env:LOCALAPPDATA "codemcp-remote"
}
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\codemcp-remote"
}

$Executable = Join-Path $InstallDir "codemcp-remote.exe"
$StateFile = Join-Path $RuntimeHome "phase5-validation.json"
$SecretFile = Join-Path $RuntimeHome "secrets\cloudflare-tunnel-token.dpapi"
$LogDir = Join-Path $RuntimeHome "logs"
$RuntimeArguments = @("--home", $RuntimeHome)

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "installed codemcp-remote.exe was not found: $Executable"
}
if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
    throw "Phase 6 requires a managed clean-machine Prepare state: $StateFile"
}

function Invoke-JsonCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = (& $Executable @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "codemcp-remote $($Arguments -join ' ') failed with exit code $exitCode`n$raw"
    }
    try { return $raw | ConvertFrom-Json }
    catch { throw "codemcp-remote did not return JSON: $($Arguments -join ' ')`n$raw" }
}

function Invoke-JsonAttempt {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = (& $Executable @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    $payload = $null
    if (-not [string]::IsNullOrWhiteSpace($raw)) {
        try { $payload = $raw | ConvertFrom-Json } catch { $payload = $null }
    }
    [pscustomobject]@{ exit_code = $exitCode; payload = $payload; raw = $raw }
}

function Assert-DoctorHealthy {
    param([Parameter(Mandatory = $true)]$Doctor)
    if ($Doctor.status -ne "ok") { throw "doctor did not report status=ok" }
    if ($Doctor.checks.configuration.worker_mode -ne "local") { throw "Phase 6 requires worker_mode=local" }
    if ($Doctor.checks.git.status -ne "ok") { throw "doctor cannot find Git" }
    if ($Doctor.checks.transport.provider -ne "cloudflare") { throw "Phase 6 default profile requires Cloudflare transport" }
    if ($Doctor.checks.tunnel_token.status -ne "ok" -or $Doctor.checks.tunnel_token.source -ne "windows-dpapi") {
        throw "doctor did not prove Cloudflare tunnel-token DPAPI recovery"
    }
    if ($Doctor.checks.auth.status -ne "ready" -or $Doctor.checks.auth.mode -ne "none") {
        throw "Phase 6 requires Profile A auth.mode=none"
    }
    if ($Doctor.checks.network_trust.status -ne "ready" -or $Doctor.checks.network_trust.mode -ne "cloudflare-chatgpt") {
        throw "Phase 6 requires cloudflare-chatgpt network trust"
    }
    if ($Doctor.checks.identity_level -ne "network-only") { throw "Phase 6 requires network-only identity semantics" }
}

function Assert-Running {
    param([Parameter(Mandatory = $true)]$Status)
    if ($Status.status -ne "running") { throw "lifecycle did not reach running state" }
    if (-not $Status.bridge.owned -or $Status.bridge.health.status -ne "ok") { throw "Bridge is not healthy and owned" }
    if (-not $Status.tunnel.owned -or $Status.tunnel.health.status -ne "ok") { throw "Tunnel is not healthy and owned" }
}

function Stop-AcceptanceRuntime {
    $attempt = Invoke-JsonAttempt -Arguments (@("stop") + $RuntimeArguments)
    if ($attempt.exit_code -ne 0) { throw "stop failed with exit code $($attempt.exit_code)`n$($attempt.raw)" }
}

function Start-And-Verify {
    $doctor = Invoke-JsonCommand -Arguments (@("doctor") + $RuntimeArguments)
    Assert-DoctorHealthy -Doctor $doctor
    $started = Invoke-JsonCommand -Arguments (@("start", "--startup-timeout", "45") + $RuntimeArguments)
    if ($started.status -ne "ok") { throw "start did not report status=ok" }
    $status = Invoke-JsonCommand -Arguments (@("status") + $RuntimeArguments)
    Assert-Running -Status $status
    return $status
}

function Assert-ProcessExited {
    param([Parameter(Mandatory = $true)][int]$ProcessId,[Parameter(Mandatory = $true)][string]$Name)
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name process did not exit: pid=$ProcessId"
}

function Assert-StartFailsWithListener {
    param([Parameter(Mandatory = $true)][int]$Port,[Parameter(Mandatory = $true)][string]$CaseName)
    $listener = New-Object System.Net.Sockets.TcpListener -ArgumentList @(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    $listener.Start()
    try {
        $attempt = Invoke-JsonAttempt -Arguments (@("start", "--startup-timeout", "10") + $RuntimeArguments)
        if ($attempt.exit_code -eq 0) { throw "$CaseName unexpectedly allowed start while an unrelated listener owned port $Port" }
        if (-not $listener.Server.IsBound) { throw "$CaseName start attempt disturbed the unrelated listener on port $Port" }
    } finally {
        $listener.Stop()
        Stop-AcceptanceRuntime
    }
}

function Scan-LogsForSecretShapes {
    if (-not (Test-Path -LiteralPath $LogDir -PathType Container)) { return 0 }
    $patterns = @(
        '(?i)TUNNEL_TOKEN\s*[:=]\s*(?!<redacted>)[^\s,;]+',
        '(?i)Authorization\s*[:=]\s*Bearer\s+(?!<redacted>)[A-Za-z0-9._~+/=-]+',
        '(?i)\bBearer\s+(?!<redacted>)[A-Za-z0-9._~+/=-]{12,}',
        '\bsk-[A-Za-z0-9_-]{12,}\b'
    )
    $findings = New-Object System.Collections.Generic.List[string]
    foreach ($file in @(Get-ChildItem -LiteralPath $LogDir -Recurse -File -ErrorAction SilentlyContinue)) {
        $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($null -eq $text) { continue }
        foreach ($pattern in $patterns) {
            if ($text -match $pattern) { $findings.Add($file.FullName); break }
        }
    }
    if ($findings.Count -gt 0) { throw "potential plaintext credential shape found in logs: $($findings -join ', ')" }
    return $findings.Count
}

$state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.acceptance_profile -ne "5.5.7A") { throw "Phase 6 mandatory profile requires acceptance_profile=5.5.7A" }
if ($state.transport -ne "cloudflare") { throw "Phase 6 mandatory profile requires Cloudflare transport" }
if ($state.auth_mode -ne "none") { throw "Phase 6 mandatory profile requires auth_mode=none" }
if ($state.network_trust_mode -ne "cloudflare-chatgpt") { throw "Phase 6 mandatory profile requires cloudflare-chatgpt network trust" }

$env:CONTROL_PLANE_API_KEY = $null
$env:TUNNEL_TOKEN = $null
$env:CODEMCP_RS_VERIFICATION_SECRET = $null

Stop-AcceptanceRuntime

$cycleResults = New-Object System.Collections.Generic.List[object]
for ($index = 1; $index -le $Iterations; $index++) {
    $status = Start-And-Verify
    $cycleResults.Add([ordered]@{
        iteration = $index
        bridge_pid = [int]$status.bridge.pid
        tunnel_pid = [int]$status.tunnel.pid
        bridge_health = [string]$status.bridge.health.status
        tunnel_health = [string]$status.tunnel.health.status
    })
    Stop-AcceptanceRuntime
}

$bridgeStatus = Start-And-Verify
$bridgePid = [int]$bridgeStatus.bridge.pid
Stop-Process -Id $bridgePid -Force
Assert-ProcessExited -ProcessId $bridgePid -Name "Bridge"
$bridgeDegraded = Invoke-JsonAttempt -Arguments (@("status") + $RuntimeArguments)
if (
    $null -ne $bridgeDegraded.payload -and
    $bridgeDegraded.payload.bridge.owned -eq $true -and
    $bridgeDegraded.payload.bridge.health.status -eq "ok"
) {
    throw "Bridge crash was not reflected in lifecycle status"
}
Stop-AcceptanceRuntime
$null = Start-And-Verify
Stop-AcceptanceRuntime

$tunnelStatus = Start-And-Verify
$tunnelPid = [int]$tunnelStatus.tunnel.pid
Stop-Process -Id $tunnelPid -Force
Assert-ProcessExited -ProcessId $tunnelPid -Name "Tunnel"
$tunnelDegraded = $null
$tunnelCrashDeadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $tunnelCrashDeadline) {
    $tunnelDegraded = Invoke-JsonAttempt -Arguments (@("status") + $RuntimeArguments)
    if (
        $null -ne $tunnelDegraded.payload -and
        $tunnelDegraded.payload.status -eq "degraded" -and
        $tunnelDegraded.payload.tunnel.owned -eq $false -and
        $tunnelDegraded.payload.tunnel.health.status -ne "ok"
    ) {
        break
    }
    Start-Sleep -Milliseconds 250
}
if (
    $null -eq $tunnelDegraded -or
    $null -eq $tunnelDegraded.payload -or
    $tunnelDegraded.payload.status -ne "degraded" -or
    $tunnelDegraded.payload.tunnel.owned -ne $false -or
    $tunnelDegraded.payload.tunnel.health.status -eq "ok"
) {
    throw "Tunnel crash was not reflected in lifecycle status and child-process health"
}
Stop-AcceptanceRuntime
$null = Start-And-Verify
Stop-AcceptanceRuntime

Assert-StartFailsWithListener -Port $BridgePort -CaseName "bridge-port-occupied"
Assert-StartFailsWithListener -Port $MetricsPort -CaseName "tunnel-metrics-port-occupied"

$originalPath = $env:PATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $gitDoctor = Invoke-JsonAttempt -Arguments (@("doctor") + $RuntimeArguments)
    if ($null -ne $gitDoctor.payload -and $gitDoctor.payload.checks.git.status -eq "ok") {
        throw "doctor unexpectedly found Git after PATH isolation"
    }
} finally {
    $env:PATH = $originalPath
}

if (-not (Test-Path -LiteralPath $SecretFile -PathType Leaf)) {
    throw "Cloudflare DPAPI secret file was not found: $SecretFile"
}
$secretBackup = "$SecretFile.phase6-backup"
if (Test-Path -LiteralPath $secretBackup) { throw "Phase 6 secret backup path already exists: $secretBackup" }
Move-Item -LiteralPath $SecretFile -Destination $secretBackup
try {
    $secretDoctor = Invoke-JsonAttempt -Arguments (@("doctor") + $RuntimeArguments)
    if ($null -ne $secretDoctor.payload -and $secretDoctor.payload.checks.tunnel_token.status -eq "ok") {
        throw "doctor unexpectedly reported the Cloudflare token as available after its DPAPI blob was removed"
    }
} finally {
    Move-Item -LiteralPath $secretBackup -Destination $SecretFile
}

$null = Start-And-Verify
Stop-AcceptanceRuntime
$logFindingCount = Scan-LogsForSecretShapes

[ordered]@{
    status = "phase6-local-host-gate-pass"
    phase = "6"
    profile = "windows11-packaged-cloudflare-network-trust"
    requested_iterations = $Iterations
    completed_iterations = $cycleResults.Count
    failed_iterations = 0
    lifecycle_cycles = $cycleResults
    bridge_abnormal_exit = "pass"
    tunnel_abnormal_exit = "pass"
    bridge_port_occupied = "pass"
    tunnel_metrics_port_occupied = "pass"
    git_unavailable_diagnostic = "pass"
    cloudflare_dpapi_secret_missing_diagnostic = "pass"
    plaintext_log_secret_shape_scan = "pass"
    log_finding_count = $logFindingCount
    runtime_left_running = $false
    remaining_remote_cases = @(
        "native worker abnormal exit during controlled mutation",
        "tunnel disconnect during mutation",
        "restart around backend mutation boundary / unknown reconciliation",
        "registered timeout child-process tree",
        "Windows path and encoding matrix through the MCP contract"
    )
    note = "This script intentionally does not claim full Phase 6 PASS until remaining remote mutation/fault/encoding cases are executed."
} | ConvertTo-Json -Depth 8
