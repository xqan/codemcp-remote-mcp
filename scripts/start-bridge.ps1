[CmdletBinding()]
param(
    [string]$BridgeConfig,
    [string]$ProjectsConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bridgeProject = Join-Path $repositoryRoot "bridge"
if ([string]::IsNullOrWhiteSpace($BridgeConfig)) {
    $BridgeConfig = Join-Path $repositoryRoot "config\bridge.example.toml"
}
if ([string]::IsNullOrWhiteSpace($ProjectsConfig)) {
    $ProjectsConfig = Join-Path $repositoryRoot "config\projects.toml"
    if (-not (Test-Path -LiteralPath $ProjectsConfig -PathType Leaf)) {
        $ProjectsConfig = Join-Path $repositoryRoot "config\projects.example.toml"
    }
}

$bridgeConfigPath = (Resolve-Path -LiteralPath $BridgeConfig).Path
$projectsConfigPath = (Resolve-Path -LiteralPath $ProjectsConfig).Path
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv was not found on PATH"
}

Write-Host "Starting loopback Bridge at http://127.0.0.1:46200/mcp"
& $uv.Source run --project $bridgeProject codemcp-bridge-server serve `
    --bridge-config $bridgeConfigPath `
    --projects-config $projectsConfigPath
exit $LASTEXITCODE
