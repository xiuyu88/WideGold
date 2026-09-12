param(
    [switch]$Analysis,
    [string]$RunId
)
$ErrorActionPreference = "Stop"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Name
    )

    if (-not (Test-Path ".env")) {
        return $null
    }

    foreach ($rawLine in Get-Content ".env") {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)
        if ($parts[0].Trim() -ne $Name) {
            continue
        }

        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        return $value
    }

    return $null
}

function Resolve-ComposePort {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Service,
        [Parameter(Mandatory=$true)]
        [int]$ContainerPort
    )

    try {
        $lines = @(& docker compose port $Service $ContainerPort 2>$null)
        if ($LASTEXITCODE -ne 0) {
            return $null
        }

        foreach ($line in $lines) {
            $text = [string]$line
            if ($text -match ':(\d+)\s*$') {
                return $Matches[1]
            }
        }
    } catch {
        return $null
    }

    return $null
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or not available on PATH."
}
if (-not (Test-Path ".env")) {
    throw ".env not found. Copy .env.example/.env.final.example to .env and configure it first."
}

docker compose config | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "docker compose config failed"
}

Write-Host "[1/3] Checking container state..."
docker compose ps
if ($LASTEXITCODE -ne 0) {
    throw "docker compose ps failed"
}

# Resolve WideGold API URL.
if (-not $env:WIDEGOLD_DOCTOR_API_URL) {
    $nginxPort = Resolve-ComposePort -Service "nginx" -ContainerPort 80

    if (-not $nginxPort) {
        $nginxPort = Get-DotEnvValue -Name "HTTP_PORT"
    }
    if (-not $nginxPort) {
        $nginxPort = "80"
    }

    if ($nginxPort -eq "80") {
        $env:WIDEGOLD_DOCTOR_API_URL = "http://localhost"
    } else {
        $env:WIDEGOLD_DOCTOR_API_URL = "http://localhost:$nginxPort"
    }
}

# Resolve Prefect host API URL.
if (-not $env:WIDEGOLD_DOCTOR_PREFECT_URL) {
    $prefectPort = Resolve-ComposePort -Service "prefect-server" -ContainerPort 4200

    if (-not $prefectPort) {
        $prefectPort = Get-DotEnvValue -Name "PREFECT_PORT"
    }
    if (-not $prefectPort) {
        $prefectPort = "4200"
    }

    $env:WIDEGOLD_DOCTOR_PREFECT_URL = "http://localhost:$prefectPort/api/health"
}

Write-Host "[2/3] Waiting for host endpoints and running doctor..."
Write-Host "  API URL     : $env:WIDEGOLD_DOCTOR_API_URL"
Write-Host "  Prefect URL : $env:WIDEGOLD_DOCTOR_PREFECT_URL"

# Wait for externally reachable API rather than only container health.
$deadline = (Get-Date).AddSeconds(90)
$apiReady = $false
$lastError = $null

do {
    try {
        $probe = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "$($env:WIDEGOLD_DOCTOR_API_URL.TrimEnd('/'))/health" `
            -TimeoutSec 3

        if ($probe.StatusCode -eq 200) {
            $apiReady = $true
            break
        }
    } catch {
        $lastError = $_.Exception.Message
    }

    Write-Host "  [WAIT] API health..."
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)

if (-not $apiReady) {
    Write-Host "  Last probe error: $lastError"
    throw "API did not become healthy within 90 seconds at $env:WIDEGOLD_DOCTOR_API_URL."
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & python scripts/doctor.py
    if ($LASTEXITCODE -ne 0) {
        throw "WideGold doctor reported a required dependency failure."
    }
} else {
    Write-Warning "Python is not installed on host; skipping host-side doctor."
}

Write-Host "[3/3] Running containerized smoke test..."
if ($RunId) {
    docker compose --profile tools run --rm `
        -e WIDEGOLD_SMOKE_TRIGGER_ANALYSIS=true `
        -e WIDEGOLD_SMOKE_RUN_ID=$RunId `
        smoke
} elseif ($Analysis) {
    docker compose --profile tools run --rm `
        -e WIDEGOLD_SMOKE_TRIGGER_ANALYSIS=true `
        smoke
} else {
    docker compose --profile tools run --rm smoke
}

if ($LASTEXITCODE -ne 0) {
    throw "WideGold Docker E2E smoke failed"
}

Write-Host "WideGold Docker E2E smoke passed."
