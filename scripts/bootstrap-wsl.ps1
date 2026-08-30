[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$WslDistribution = "Ubuntu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bridgeProject = Join-Path $repositoryRoot "bridge"
$localDir = Join-Path $repositoryRoot ".local"
$requirementsPath = Join-Path $localDir "worker-requirements.txt"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv was not found on PATH"
}
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($null -eq $wsl) {
    throw "wsl.exe was not found; install/enable WSL2 before bootstrapping the worker"
}

$distributions = @(
    & $wsl.Source --list --quiet 2>$null |
        ForEach-Object { ([string]$_).Replace([char]0, "").Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($LASTEXITCODE -ne 0 -or $WslDistribution -notin $distributions) {
    throw "WSL distribution '$WslDistribution' was not found"
}

$pythonProbe = (& $wsl.Source `
    --distribution $WslDistribution `
    -- `
    python3 -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 12) else 2)" `
    2>&1 | Out-String).Trim()
$pythonProbeExit = $LASTEXITCODE
if ($pythonProbeExit -ne 0) {
    throw "WSL python3 >= 3.12 is required in '$WslDistribution'; probe output: $pythonProbe"
}

New-Item -ItemType Directory -Path $localDir -Force | Out-Null
$exportOutput = (& $uv.Source export `
    --project $bridgeProject `
    --no-dev `
    --no-emit-project `
    --format requirements-txt `
    --output-file $requirementsPath `
    2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "uv export failed: $exportOutput"
}

$wslRepositoryRoot = (& $wsl.Source `
    --distribution $WslDistribution `
    -- `
    wslpath -a $repositoryRoot 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslRepositoryRoot)) {
    throw "failed to map repository path into WSL"
}
$wslRepositoryRoot = $wslRepositoryRoot.Replace("\", "/")
$wslVenv = "$wslRepositoryRoot/.local/bridge-venv-wsl"
$wslRequirements = "$wslRepositoryRoot/.local/worker-requirements.txt"
$wslPython = "$wslVenv/bin/python"

$venvOutput = (& $wsl.Source `
    --distribution $WslDistribution `
    -- `
    python3 -m venv $wslVenv 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "failed to create WSL worker virtual environment; ensure python3-venv is installed: $venvOutput"
}

$installOutput = (& $wsl.Source `
    --distribution $WslDistribution `
    -- `
    $wslPython -m pip install --disable-pip-version-check -r $wslRequirements `
    2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "failed to install locked worker dependencies: $installOutput"
}

$verifyOutput = (& $wsl.Source `
    --distribution $WslDistribution `
    -- `
    $wslPython -c "import importlib.metadata as m; v=m.version('codemcp'); print(v); raise SystemExit(0 if v == '0.3.0' else 3)" `
    2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "WSL worker verification failed: $verifyOutput"
}

[ordered]@{
    status = "ok"
    repository_root = $repositoryRoot
    wsl_distribution = $WslDistribution
    wsl_python_version = $pythonProbe
    worker_python = $wslPython
    codemcp_version = $verifyOutput
    dependency_source = "bridge/uv.lock via uv export"
    requirements_file = $requirementsPath
    note = "worker requirements are generated under .local and are not release source"
} | ConvertTo-Json -Depth 6

exit 0
