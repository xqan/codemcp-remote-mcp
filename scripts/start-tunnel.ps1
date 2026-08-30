[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$ProfileName,
    [string]$ProfileDir,
    [string]$BridgeUrl,
    [string]$LogDir,
    [string]$HealthListenAddress,
    [switch]$Initialize,
    [switch]$Force
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
if ([string]::IsNullOrWhiteSpace($HealthListenAddress)) {
    $HealthListenAddress = $settings.HealthListenAddress
}
$HealthListenAddress = Assert-Phase5HealthListenAddress -Value $HealthListenAddress
if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = ".local/logs"
}
$logDirectoryPath = Resolve-Phase5Path -RepositoryRoot $repositoryRoot -Value $LogDir
$tunnelLogPath = Join-Path $logDirectoryPath "tunnel-client.log"
Assert-Phase5TunnelId -TunnelId $settings.TunnelId
if (-not $settings.ApiKeyPresent) {
    throw "CONTROL_PLANE_API_KEY is not set; inject it from a secret store or process environment"
}

$tunnelClient = Get-Phase5TunnelClient
New-Item -ItemType Directory -Path $settings.ProfileDir -Force | Out-Null
$logWriter = New-Phase6LogWriter -Path $tunnelLogPath

function Invoke-Phase6LoggedTunnelClient {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [System.IO.TextWriter]$Writer
    )

    & $tunnelClient.Source @Arguments 2>&1 | ForEach-Object {
        Write-Host (Write-Phase6LogLine -Writer $Writer -Value $_)
    }
    return [int]$LASTEXITCODE
}

$exitCode = 0
try {
    if ($Initialize) {
        $initArguments = @(
            "init",
            "--sample", "sample_mcp_remote_no_auth",
            "--profile", $settings.ProfileName,
            "--profile-dir", $settings.ProfileDir,
            "--tunnel-id", $settings.TunnelId,
            "--mcp-server-url", $settings.BridgeUrl,
            "--health-listen-addr", $HealthListenAddress,
            "--control-plane-api-key-ref", "env:CONTROL_PLANE_API_KEY"
        )
        if ($Force) {
            $initArguments += "--force"
        }
        $exitCode = Invoke-Phase6LoggedTunnelClient -Arguments $initArguments -Writer $logWriter
    }

    if ($exitCode -eq 0) {
        $profilePath = Get-Phase5ProfilePath `
            -ProfileDir $settings.ProfileDir `
            -ProfileName $settings.ProfileName
        Assert-Phase5ProfileContract `
            -ProfilePath $profilePath `
            -TunnelId $settings.TunnelId `
            -BridgeUrl $settings.BridgeUrl

        $bridgeUri = [Uri]$settings.BridgeUrl
        $bridgeHealthUrl = "{0}://{1}/healthz" -f $bridgeUri.Scheme, $bridgeUri.Authority
        $bridgeHealth = Test-Phase5HttpEndpoint -Url $bridgeHealthUrl
        if ($bridgeHealth.status -ne "ok") {
            throw "Bridge health check failed at $bridgeHealthUrl; start scripts/start-bridge.ps1 first"
        }

        $startMessage = "Starting tunnel-client profile '{0}'. Health: http://127.0.0.1:{1}" -f `
            $settings.ProfileName, ($HealthListenAddress -replace '^127\.0\.0\.1:', '')
        Write-Host (Write-Phase6LogLine -Writer $logWriter -Value $startMessage)
        $runArguments = @(
            "run",
            "--profile", $settings.ProfileName,
            "--profile-dir", $settings.ProfileDir,
            "--health.listen-addr", $HealthListenAddress
        )
        $exitCode = Invoke-Phase6LoggedTunnelClient -Arguments $runArguments -Writer $logWriter
    }
} finally {
    $logWriter.Dispose()
}
exit $exitCode
