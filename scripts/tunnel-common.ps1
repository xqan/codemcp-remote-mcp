Set-StrictMode -Version Latest

$script:Phase6LogMaxBytes = 5MB
$script:Phase6LogBackupCount = 3

function Redact-Phase6LogText {
    param(
        [AllowNull()]
        [string]$Value
    )

    if ($null -eq $Value) {
        return ""
    }

    $redacted = [regex]::Replace(
        $Value,
        '(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+',
        'Bearer <redacted>'
    )
    $keyPrefix = "(?i)((?:[\`"']?\b(?:CONTROL_PLANE_API_KEY|OPENAI_API_KEY|API_KEY|AUTHORIZATION|ACCESS_TOKEN|REFRESH_TOKEN|TOKEN)\b[\`"']?)\s*[:=]\s*)"
    $quotedValuePattern = $keyPrefix + "(\`"[^\`"]*\`"|'[^']*')"
    $redacted = [regex]::Replace($redacted, $quotedValuePattern, '$1<redacted>')
    $redacted = [regex]::Replace($redacted, $keyPrefix + '([^\s,;]+)', '$1<redacted>')
    return [regex]::Replace($redacted, '(?i)\bsk-[A-Za-z0-9_-]{8,}', '<redacted-api-key>')
}

function Rotate-Phase6Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    if ((Get-Item -LiteralPath $Path).Length -lt $script:Phase6LogMaxBytes) {
        return
    }

    for ($index = $script:Phase6LogBackupCount - 1; $index -ge 1; $index--) {
        $source = "{0}.{1}" -f $Path, $index
        $destination = "{0}.{1}" -f $Path, ($index + 1)
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            [System.IO.File]::Move($source, $destination, $true)
        }
    }
    [System.IO.File]::Move($Path, ("{0}.1" -f $Path), $true)
}

function New-Phase6LogWriter {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Rotate-Phase6Log -Path $Path
    $fileStream = [System.IO.FileStream]::new(
        $Path,
        [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::ReadWrite
    )
    $writer = [System.IO.StreamWriter]::new(
        $fileStream,
        [System.Text.UTF8Encoding]::new($false)
    )
    $writer.AutoFlush = $true
    return $writer
}

function Write-Phase6LogLine {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.TextWriter]$Writer,
        [AllowNull()]
        [object]$Value
    )

    $text = if ($null -eq $Value) { "" } else { [string]$Value }
    $redacted = Redact-Phase6LogText -Value $text
    $timestamp = [DateTime]::UtcNow.ToString(
        "o",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    $Writer.WriteLine(("{0} {1}" -f $timestamp, $redacted))
    return $redacted
}

function Get-Phase5TomlString {
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

function Get-Phase5RepositoryRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Import-Phase5EnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    $allowedNames = @(
        "CONTROL_PLANE_TUNNEL_ID",
        "CONTROL_PLANE_API_KEY",
        "TUNNEL_CLIENT_PROFILE",
        "TUNNEL_CLIENT_PROFILE_DIR",
        "BRIDGE_MCP_URL",
        "HEALTH_LISTEN_ADDR",
        "TUNNEL_HEALTH_URL",
        "CONTROL_PLANE_ORGANIZATION_ID"
    )
    $lineNumber = 0
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber++
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch '^(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$') {
            throw "invalid environment assignment in $Path at line $lineNumber"
        }

        $name = $Matches["name"]
        $value = $Matches["value"].Trim()
        if ($name -notin $allowedNames) {
            throw "$name is not an allowed Phase 5 environment setting"
        }
        if ($name -eq "CONTROL_PLANE_API_KEY") {
            throw "CONTROL_PLANE_API_KEY must be injected into the process, not stored in $Path"
        }
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        } elseif ($value.Contains('"') -or $value.Contains("'")) {
            throw "unterminated quoted value in $Path at line $lineNumber"
        }

        # Empty assignments never erase a secret or an existing process value.
        if ($value.Length -gt 0) {
            Set-Item -Path ("Env:{0}" -f $name) -Value $value
        }
    }
}

function Resolve-Phase5Path {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if ($expanded.StartsWith("~")) {
        $expanded = Join-Path $HOME $expanded.Substring(1).TrimStart("/", "\")
    }
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $expanded))
}

function Get-Phase5Settings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$EnvFile,
        [string]$ProfileName,
        [string]$ProfileDir,
        [string]$BridgeUrl,
        [string]$TunnelHealthUrl
    )

    Import-Phase5EnvFile -Path $EnvFile

    $effectiveProfileName = $ProfileName
    if ([string]::IsNullOrWhiteSpace($effectiveProfileName)) {
        $effectiveProfileName = $env:TUNNEL_CLIENT_PROFILE
    }
    if ([string]::IsNullOrWhiteSpace($effectiveProfileName)) {
        $effectiveProfileName = "codemcp-bridge"
    }
    if ($effectiveProfileName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') {
        throw "profile name must contain only letters, digits, '.', '_' or '-'"
    }

    $effectiveProfileDir = $ProfileDir
    if ([string]::IsNullOrWhiteSpace($effectiveProfileDir)) {
        $effectiveProfileDir = $env:TUNNEL_CLIENT_PROFILE_DIR
    }
    if ([string]::IsNullOrWhiteSpace($effectiveProfileDir)) {
        $effectiveProfileDir = ".local/tunnel-client"
    }
    $resolvedProfileDir = Resolve-Phase5Path -RepositoryRoot $RepositoryRoot -Value $effectiveProfileDir

    $effectiveBridgeUrl = $BridgeUrl
    if ([string]::IsNullOrWhiteSpace($effectiveBridgeUrl)) {
        $effectiveBridgeUrl = $env:BRIDGE_MCP_URL
    }
    if ([string]::IsNullOrWhiteSpace($effectiveBridgeUrl)) {
        $effectiveBridgeUrl = "http://127.0.0.1:46200/mcp"
    }
    $normalizedBridgeUrl = Assert-Phase5BridgeUrl -Value $effectiveBridgeUrl

    $effectiveHealthUrl = $TunnelHealthUrl
    if ([string]::IsNullOrWhiteSpace($effectiveHealthUrl)) {
        $effectiveHealthUrl = $env:TUNNEL_HEALTH_URL
    }
    if ([string]::IsNullOrWhiteSpace($effectiveHealthUrl)) {
        $effectiveHealthUrl = "http://127.0.0.1:46201"
    }
    $normalizedHealthUrl = Assert-Phase5HealthUrl -Value $effectiveHealthUrl

    $healthListenAddress = $env:HEALTH_LISTEN_ADDR
    if ([string]::IsNullOrWhiteSpace($healthListenAddress)) {
        $healthListenAddress = "127.0.0.1:46201"
    }
    $healthListenAddress = Assert-Phase5HealthListenAddress -Value $healthListenAddress

    $tunnelId = $env:CONTROL_PLANE_TUNNEL_ID
    if ([string]::IsNullOrWhiteSpace($tunnelId)) {
        $tunnelId = $null
    }
    $apiKeyPresent = -not [string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)

    return [pscustomobject]@{
        EnvFile = $EnvFile
        ProfileName = $effectiveProfileName
        ProfileDir = $resolvedProfileDir
        ProfilePath = Join-Path $resolvedProfileDir ("{0}.yaml" -f $effectiveProfileName)
        BridgeUrl = $normalizedBridgeUrl
        TunnelHealthUrl = $normalizedHealthUrl
        HealthListenAddress = $healthListenAddress
        TunnelId = $tunnelId
        ApiKeyPresent = $apiKeyPresent
    }
}

function Assert-Phase5BridgeUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    try {
        $uri = [Uri]$Value
    } catch {
        throw "Bridge MCP URL is not a valid URI"
    }
    if ($uri.Scheme -notin @("http", "https") -or
        $uri.Host -ne "127.0.0.1" -or
        [string]::IsNullOrWhiteSpace($uri.AbsolutePath) -or
        $uri.AbsolutePath.TrimEnd("/") -ne "/mcp" -or
        $uri.Query -or
        $uri.Fragment) {
        throw "Bridge MCP URL must be an HTTP(S) /mcp endpoint on loopback 127.0.0.1"
    }
    return $uri.GetLeftPart([UriPartial]::Path).TrimEnd("/")
}

function Assert-Phase5HealthUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    try {
        $uri = [Uri]$Value
    } catch {
        throw "Tunnel health URL is not a valid URI"
    }
    if ($uri.Scheme -notin @("http", "https") -or
        $uri.Host -ne "127.0.0.1" -or
        ($uri.AbsolutePath -ne "/" -and $uri.AbsolutePath -ne "") -or
        $uri.Query -or
        $uri.Fragment) {
        throw "Tunnel health URL must be a loopback HTTP(S) base URL"
    }
    return $uri.GetLeftPart([UriPartial]::Authority).TrimEnd("/")
}

function Assert-Phase5HealthListenAddress {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    if ($Value -notmatch '^127\.0\.0\.1:(?<port>\d+)$') {
        throw "HEALTH_LISTEN_ADDR must bind the tunnel admin surface to 127.0.0.1"
    }
    $port = [int]$Matches["port"]
    if ($port -lt 0 -or $port -gt 65535) {
        throw "HEALTH_LISTEN_ADDR port must be between 0 and 65535"
    }
    return $Value
}

function Get-Phase5TunnelClient {
    $command = Get-Command tunnel-client -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "tunnel-client was not found on PATH"
    }
    return $command
}

function Assert-Phase5TunnelId {
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [AllowEmptyString()]
        [string]$TunnelId
    )

    if ([string]::IsNullOrWhiteSpace($TunnelId) -or
        $TunnelId -notmatch '^tunnel_[A-Za-z0-9_-]{8,}$' -or
        $TunnelId -match '(?i)replace|example|placeholder') {
        throw "CONTROL_PLANE_TUNNEL_ID must be a real tunnel_id from OpenAI Platform"
    }
}

function Get-Phase5ProfilePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProfileDir,
        [Parameter(Mandatory = $true)]
        [string]$ProfileName
    )

    $yamlPath = Join-Path $ProfileDir ("{0}.yaml" -f $ProfileName)
    if (Test-Path -LiteralPath $yamlPath -PathType Leaf) {
        return $yamlPath
    }
    $ymlPath = Join-Path $ProfileDir ("{0}.yml" -f $ProfileName)
    if (Test-Path -LiteralPath $ymlPath -PathType Leaf) {
        return $ymlPath
    }
    return $yamlPath
}

function Assert-Phase5ProfileContract {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProfilePath,
        [Parameter(Mandatory = $true)]
        [string]$TunnelId,
        [Parameter(Mandatory = $true)]
        [string]$BridgeUrl
    )

    if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) {
        throw "Tunnel profile not found: $ProfilePath"
    }
    $content = Get-Content -LiteralPath $ProfilePath -Raw
    $quotedTunnelId = [regex]::Escape($TunnelId)
    if ($content -notmatch "(?m)^\s*tunnel_id:\s*[`"']?$quotedTunnelId[`"']?\s*$") {
        throw "Tunnel profile tunnel_id does not match CONTROL_PLANE_TUNNEL_ID"
    }
    if ($content -notmatch '(?m)^\s*base_url:\s*["'']?https://(api|mtls)\.openai\.com["'']?\s*$') {
        throw "Tunnel profile control plane must be api.openai.com or mtls.api.openai.com"
    }
    if ($content -notmatch '(?m)^\s*api_key:\s*["'']?env:CONTROL_PLANE_API_KEY["'']?\s*$') {
        throw "Tunnel profile must reference env:CONTROL_PLANE_API_KEY, not store a key"
    }
    if ($content -notmatch '(?m)^\s*server_urls:\s*$') {
        throw "Tunnel profile must use an HTTP MCP server URL"
    }
    if ($content -match '(?m)^\s*commands:\s*$') {
        throw "stdio MCP commands are not allowed by this Bridge tunnel wrapper"
    }

    $urlMatches = [regex]::Matches(
        $content,
        '(?m)^\s+url:\s*["'']?(?<url>[^\r\n"''\s]+)["'']?\s*$'
    )
    $profileUrls = @($urlMatches | ForEach-Object { $_.Groups["url"].Value })
    if ($profileUrls.Count -ne 1 -or $profileUrls[0] -ne $BridgeUrl) {
        throw "Tunnel profile must contain exactly one MCP URL matching $BridgeUrl"
    }
}

function Test-Phase5HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 5 -UseBasicParsing
        return [pscustomobject]@{
            status = "ok"
            status_code = [int]$response.StatusCode
            url = $Url
        }
    } catch {
        return [pscustomobject]@{
            status = "unreachable"
            status_code = $null
            url = $Url
            error = $_.Exception.Message
        }
    }
}

function Protect-Phase5DiagnosticText {
    param(
        [AllowNull()]
        [string]$Text
    )

    if ($null -eq $Text) {
        return ""
    }
    $protected = $Text -replace '(?i)(CONTROL_PLANE_API_KEY|OPENAI_API_KEY|api_key|authorization|token)\s*[:=]\s*("[^"]*"|''[^'']*''|\S+)', '$1=<redacted>'
    return $protected -replace '(?i)sk-[A-Za-z0-9_-]+', '<redacted-api-key>'
}
