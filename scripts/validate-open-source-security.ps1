[CmdletBinding()]
param(
    [string]$ArtifactPath,
    [switch]$RequireArtifact
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$auditRoot = Join-Path $repositoryRoot ".local\security-audit"
$currentTreeRoot = Join-Path $auditRoot "current-tree"
$artifactRoot = Join-Path $auditRoot "artifact"
New-Item -ItemType Directory -Force -Path $auditRoot | Out-Null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    & $Action
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$Label failed with exit code $code"
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv is required for dependency vulnerability audit"
}
$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    throw "Git is required for source/history audit"
}

$gitleaksJson = (& pwsh -NoLogo -NoProfile -NonInteractive `
    -File (Join-Path $repositoryRoot "scripts\prepare-gitleaks.ps1") | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Gitleaks preparation failed"
}
$gitleaksInfo = $gitleaksJson | ConvertFrom-Json
$gitleaks = [string]$gitleaksInfo.executable

$dependencyReport = Join-Path $auditRoot "uv-audit.txt"
$dependencyOutput = (& $uv.Source audit --project (Join-Path $repositoryRoot "bridge") --frozen 2>&1 | Out-String).Trim()
$dependencyOutput | Set-Content -LiteralPath $dependencyReport -Encoding utf8
if ($LASTEXITCODE -ne 0) {
    throw "dependency vulnerability audit failed; see $dependencyReport"
}

$licenseReport = Join-Path $auditRoot "dependency-licenses.json"
$licenseScript = Join-Path $repositoryRoot "scripts\dependency_license_audit.py"
$licenseOutput = (& $uv.Source run --project (Join-Path $repositoryRoot "bridge") --frozen python `
    $licenseScript --lock (Join-Path $repositoryRoot "bridge\uv.lock") --output $licenseReport 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "dependency license evidence audit failed: $licenseOutput"
}

Remove-Item -LiteralPath $currentTreeRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $currentTreeRoot | Out-Null
$currentTreeArchive = Join-Path $auditRoot "current-tree.zip"
Remove-Item -LiteralPath $currentTreeArchive -Force -ErrorAction SilentlyContinue
Invoke-Checked -Label "git archive current tree" -Action {
    & $git.Source -C $repositoryRoot archive --format=zip --output=$currentTreeArchive HEAD
}
Expand-Archive -LiteralPath $currentTreeArchive -DestinationPath $currentTreeRoot -Force

$currentTreeReport = Join-Path $auditRoot "gitleaks-current-tree.json"
Remove-Item -LiteralPath $currentTreeReport -Force -ErrorAction SilentlyContinue
Invoke-Checked -Label "Gitleaks current tracked tree scan" -Action {
    & $gitleaks dir --redact=100 --no-banner --report-format json --report-path $currentTreeReport $currentTreeRoot
}

$currentOperatorMarkers = @(
    ("quick" + "clip.cc").ToLowerInvariant(),
    ("D:" + "\Documents\CodexProject").ToLowerInvariant(),
    ("D:" + "/Documents/CodexProject").ToLowerInvariant()
)
$currentTextExtensions = @(
    ".cfg", ".cmd", ".html", ".ini", ".json", ".md", ".ps1", ".py",
    ".sha256", ".toml", ".txt", ".xml", ".yaml", ".yml"
)
$currentPrivacyViolations = New-Object System.Collections.Generic.List[string]
foreach ($file in Get-ChildItem -LiteralPath $currentTreeRoot -Recurse -File -ErrorAction Stop) {
    $relative = $file.FullName.Substring($currentTreeRoot.Length).TrimStart("\").Replace("\", "/")
    if ($relative.StartsWith("docs/releases/") -or $relative.StartsWith("docs/reports/")) {
        continue
    }
    if ($file.Length -gt 8MB -or $file.Extension.ToLowerInvariant() -notin $currentTextExtensions) {
        continue
    }
    try {
        $content = [System.IO.File]::ReadAllText($file.FullName).ToLowerInvariant()
    } catch {
        continue
    }
    foreach ($marker in $currentOperatorMarkers) {
        if ($content.Contains($marker)) {
            $currentPrivacyViolations.Add($relative)
            break
        }
    }
}
if ($currentPrivacyViolations.Count -gt 0) {
    throw "current tracked tree contains operator-specific deployment/path data: $($currentPrivacyViolations -join ', ')"
}

$historyReport = Join-Path $auditRoot "gitleaks-history.json"
Remove-Item -LiteralPath $historyReport -Force -ErrorAction SilentlyContinue
Invoke-Checked -Label "Gitleaks full Git history scan" -Action {
    & $gitleaks git --redact=100 --no-banner --report-format json --report-path $historyReport `
        --log-opts=--all $repositoryRoot
}

$artifactStatus = "not-run"
$artifactReport = $null
if ([string]::IsNullOrWhiteSpace($ArtifactPath)) {
    if ($RequireArtifact) {
        $ArtifactPath = Join-Path $repositoryRoot ".local\release-candidate\codemcp-remote-v0.1.0-windows-x64.zip"
    }
}
if (-not [string]::IsNullOrWhiteSpace($ArtifactPath)) {
    $resolvedArtifact = [System.IO.Path]::GetFullPath($ArtifactPath)
    if (-not (Test-Path -LiteralPath $resolvedArtifact)) {
        throw "artifact path does not exist: $resolvedArtifact"
    }

    Remove-Item -LiteralPath $artifactRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
    $scanTarget = $resolvedArtifact

    if (Test-Path -LiteralPath $resolvedArtifact -PathType Leaf) {
        if ([System.IO.Path]::GetExtension($resolvedArtifact) -ne ".zip") {
            throw "full artifact audit requires a directory or ZIP staging artifact"
        }
        Expand-Archive -LiteralPath $resolvedArtifact -DestinationPath $artifactRoot -Force
        $scanTarget = $artifactRoot
    }

    $forbiddenArtifactNames = @(
        "projects.toml",
        "tunnel-profile.local.env",
        "remote.toml",
        "tunnel.env",
        "bridge.sqlite3"
    )
    $violations = @(
        Get-ChildItem -LiteralPath $scanTarget -Recurse -File -ErrorAction Stop |
            Where-Object {
                $_.Name -in $forbiddenArtifactNames -or
                $_.Name -like "*.dpapi" -or
                $_.Name -like "*.sqlite3*" -or
                $_.Name -like "*.log"
            } |
            ForEach-Object { $_.FullName }
    )
    if ($violations.Count -gt 0) {
        throw "artifact contains forbidden runtime/secret material: $($violations -join ', ')"
    }

    $operatorMarkers = @(
        ("quick" + "clip.cc").ToLowerInvariant(),
        ("D:" + "\Documents\CodexProject").ToLowerInvariant(),
        ("D:" + "/Documents/CodexProject").ToLowerInvariant()
    )
    $textExtensions = @(
        ".cfg", ".cmd", ".html", ".ini", ".json", ".md", ".ps1", ".py",
        ".sha256", ".toml", ".txt", ".xml", ".yaml", ".yml"
    )
    $privacyViolations = New-Object System.Collections.Generic.List[string]
    foreach ($file in Get-ChildItem -LiteralPath $scanTarget -Recurse -File -ErrorAction Stop) {
        if ($file.Length -gt 8MB -or $file.Extension.ToLowerInvariant() -notin $textExtensions) {
            continue
        }
        try {
            $content = [System.IO.File]::ReadAllText($file.FullName).ToLowerInvariant()
        } catch {
            continue
        }
        foreach ($marker in $operatorMarkers) {
            if ($content.Contains($marker)) {
                $privacyViolations.Add($file.FullName)
                break
            }
        }
    }
    if ($privacyViolations.Count -gt 0) {
        throw "artifact contains operator-specific deployment/path data: $($privacyViolations -join ', ')"
    }

    $artifactReport = Join-Path $auditRoot "gitleaks-artifact.json"
    Remove-Item -LiteralPath $artifactReport -Force -ErrorAction SilentlyContinue
    Invoke-Checked -Label "Gitleaks artifact scan" -Action {
        & $gitleaks dir --redact=100 --no-banner --report-format json --report-path $artifactReport $scanTarget
    }
    $artifactStatus = "passed"
}

[ordered]@{
    status = "ok"
    dependency_audit = "passed"
    dependency_license_evidence = "passed"
    dependency_license_compatibility_review = "manual-required"
    current_tree_secret_scan = "passed"
    git_history_secret_scan = "passed"
    artifact_scan = $artifactStatus
    gitleaks_version = [string]$gitleaksInfo.version
    reports = [ordered]@{
        dependency = $dependencyReport
        dependency_licenses = $licenseReport
        current_tree = $currentTreeReport
        history = $historyReport
        artifact = $artifactReport
    }
} | ConvertTo-Json -Depth 5
