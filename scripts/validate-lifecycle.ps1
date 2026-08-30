[CmdletBinding()]
param(
    [ValidateRange(1, 100)]
    [int]$Iterations = 20,
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
    [switch]$InitializeFirst
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "tunnel-common.ps1")

$repositoryRoot = Get-Phase5RepositoryRoot
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($null -eq $pwsh) {
    throw "pwsh was not found on PATH"
}

$validationRoot = Join-Path $repositoryRoot ".local\validation"
$runId = [DateTime]::UtcNow.ToString(
    "yyyyMMddTHHmmssfffZ",
    [System.Globalization.CultureInfo]::InvariantCulture
)
$outputDir = Join-Path $validationRoot ("phase6-lifecycle-{0}" -f $runId)
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$startScript = Join-Path $repositoryRoot "scripts\start-all.ps1"
$doctorScript = Join-Path $repositoryRoot "scripts\doctor.ps1"
$stopScript = Join-Path $repositoryRoot "scripts\stop-all.ps1"

function Add-Phase6OptionalArgument {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.List[string]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [AllowNull()]
        [string]$Value
    )

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        [void]$Arguments.Add($Name)
        [void]$Arguments.Add($Value)
    }
}

function Get-Phase6StartArguments {
    param([bool]$Initialize)

    $arguments = [System.Collections.Generic.List[string]]::new()
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-BridgeConfig" -Value $BridgeConfig
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProjectsConfig" -Value $ProjectsConfig
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-EnvFile" -Value $EnvFile
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileName" -Value $ProfileName
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileDir" -Value $ProfileDir
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-BridgeUrl" -Value $BridgeUrl
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-TunnelHealthUrl" -Value $TunnelHealthUrl
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-LogDir" -Value $LogDir
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-HealthListenAddress" -Value $HealthListenAddress
    [void]$arguments.Add("-StartupTimeoutSec")
    [void]$arguments.Add([string]$StartupTimeoutSec)
    if ($Initialize) {
        [void]$arguments.Add("-Initialize")
    }
    return $arguments.ToArray()
}

function Get-Phase6DoctorArguments {
    $arguments = [System.Collections.Generic.List[string]]::new()
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-BridgeConfig" -Value $BridgeConfig
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProjectsConfig" -Value $ProjectsConfig
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-EnvFile" -Value $EnvFile
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileName" -Value $ProfileName
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileDir" -Value $ProfileDir
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-BridgeUrl" -Value $BridgeUrl
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-TunnelHealthUrl" -Value $TunnelHealthUrl
    return $arguments.ToArray()
}

function Get-Phase6StopArguments {
    $arguments = [System.Collections.Generic.List[string]]::new()
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-EnvFile" -Value $EnvFile
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileName" -Value $ProfileName
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-ProfileDir" -Value $ProfileDir
    Add-Phase6OptionalArgument -Arguments $arguments -Name "-BridgeUrl" -Value $BridgeUrl
    return $arguments.ToArray()
}

function Invoke-Phase6ValidationStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$EvidencePath
    )

    $startedAt = [DateTime]::UtcNow
    $rawOutput = (& $pwsh.Source `
        -NoLogo `
        -NoProfile `
        -NonInteractive `
        -File $ScriptPath `
        @Arguments 2>&1 | Out-String).Trim()
    $exitCode = $LASTEXITCODE
    $safeOutput = Protect-Phase5DiagnosticText -Text (Redact-Phase6LogText -Value $rawOutput)
    [System.IO.File]::WriteAllText(
        $EvidencePath,
        $safeOutput,
        [System.Text.UTF8Encoding]::new($false)
    )
    $finishedAt = [DateTime]::UtcNow

    return [ordered]@{
        name = $Name
        status = if ($exitCode -eq 0) { "ok" } else { "failed" }
        exit_code = $exitCode
        started_at = $startedAt.ToString("o")
        finished_at = $finishedAt.ToString("o")
        duration_ms = [int][Math]::Round(($finishedAt - $startedAt).TotalMilliseconds)
        evidence = $EvidencePath
    }
}

$iterationsResult = [System.Collections.Generic.List[object]]::new()
$status = "ok"
$errorMessage = $null
$cleanup = $null

try {
    for ($iteration = 1; $iteration -le $Iterations; $iteration++) {
        $iterationStarted = [DateTime]::UtcNow
        $prefix = "iteration-{0:D2}" -f $iteration

        $start = Invoke-Phase6ValidationStep `
            -Name "start" `
            -ScriptPath $startScript `
            -Arguments (Get-Phase6StartArguments -Initialize ($InitializeFirst -and $iteration -eq 1)) `
            -EvidencePath (Join-Path $outputDir ("{0}-start.json" -f $prefix))
        if ($start.exit_code -ne 0) {
            throw "iteration $iteration start-all.ps1 failed; see $($start.evidence)"
        }

        $doctor = Invoke-Phase6ValidationStep `
            -Name "doctor" `
            -ScriptPath $doctorScript `
            -Arguments (Get-Phase6DoctorArguments) `
            -EvidencePath (Join-Path $outputDir ("{0}-doctor.json" -f $prefix))
        if ($doctor.exit_code -ne 0) {
            throw "iteration $iteration doctor.ps1 failed; see $($doctor.evidence)"
        }

        $stop = Invoke-Phase6ValidationStep `
            -Name "stop" `
            -ScriptPath $stopScript `
            -Arguments (Get-Phase6StopArguments) `
            -EvidencePath (Join-Path $outputDir ("{0}-stop.json" -f $prefix))
        if ($stop.exit_code -ne 0) {
            throw "iteration $iteration stop-all.ps1 failed; see $($stop.evidence)"
        }

        $iterationFinished = [DateTime]::UtcNow
        $iterationsResult.Add([ordered]@{
            iteration = $iteration
            status = "ok"
            duration_ms = [int][Math]::Round(($iterationFinished - $iterationStarted).TotalMilliseconds)
            start = $start
            doctor = $doctor
            stop = $stop
        })
    }
} catch {
    $status = "failed"
    $errorMessage = Protect-Phase5DiagnosticText -Text (
        Redact-Phase6LogText -Value $_.Exception.Message
    )
    try {
        $cleanup = Invoke-Phase6ValidationStep `
            -Name "cleanup" `
            -ScriptPath $stopScript `
            -Arguments (Get-Phase6StopArguments) `
            -EvidencePath (Join-Path $outputDir "cleanup.json")
    } catch {
        $cleanup = [ordered]@{
            name = "cleanup"
            status = "failed"
            error = Protect-Phase5DiagnosticText -Text (
                Redact-Phase6LogText -Value $_.Exception.Message
            )
        }
    }
}

$summary = [ordered]@{
    phase = "6"
    validation = "lifecycle"
    status = $status
    run_id = $runId
    repository_root = $repositoryRoot
    requested_iterations = $Iterations
    completed_iterations = $iterationsResult.Count
    initialize_first = [bool]$InitializeFirst
    evidence_dir = $outputDir
    iterations = $iterationsResult
    cleanup = $cleanup
    error = $errorMessage
}

$summaryJson = $summary | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText(
    (Join-Path $outputDir "summary.json"),
    $summaryJson,
    [System.Text.UTF8Encoding]::new($false)
)
$summaryJson

if ($status -ne "ok" -or $iterationsResult.Count -ne $Iterations) {
    exit 1
}
exit 0
