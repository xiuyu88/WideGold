$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or not available on PATH."
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Configure secrets before production use."
}

$envMap = @{}
Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        $envMap[$parts[0].Trim()] = $parts[1].Trim()
    }
}
$authMode = if ($envMap.ContainsKey("WIDEGOLD_AUTH_MODE")) { $envMap["WIDEGOLD_AUTH_MODE"] } else { "session" }
if ($authMode -eq "session") {
    if (-not $envMap.ContainsKey("WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD") -or [string]::IsNullOrWhiteSpace($envMap["WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD"])) {
        throw "Set WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD in .env before production start."
    }
    if ($envMap["WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD"].Length -lt 8) {
        throw "WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters."
    }
}
$pgPassword = if ($envMap.ContainsKey("POSTGRES_PASSWORD")) { $envMap["POSTGRES_PASSWORD"] } else { "" }
if ([string]::IsNullOrWhiteSpace($pgPassword) -or $pgPassword -in @("change-me", "widegold")) {
    throw "Replace POSTGRES_PASSWORD in .env with a strong unique password."
}

docker compose config | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker compose config failed" }

docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

$httpPort = if ($envMap.ContainsKey("HTTP_PORT") -and -not [string]::IsNullOrWhiteSpace($envMap["HTTP_PORT"])) { $envMap["HTTP_PORT"] } else { "80" }
$prefectPort = if ($envMap.ContainsKey("PREFECT_PORT") -and -not [string]::IsNullOrWhiteSpace($envMap["PREFECT_PORT"])) { $envMap["PREFECT_PORT"] } else { "4200" }
$dashboardUrl = if ($httpPort -eq "80") { "http://localhost" } else { "http://localhost:$httpPort" }

Write-Host "WideGold containers started."
Write-Host "Dashboard: $dashboardUrl"
Write-Host "Prefect:   http://localhost:$prefectPort"
Write-Host "Run: docker compose ps"
Write-Host "Logs: docker compose logs -f --tail=200 prefect-worker"
