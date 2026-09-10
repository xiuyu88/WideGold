$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker，请先启动 Docker Desktop。"
}

if (-not (Test-Path ".env")) {
    Write-Host "未发现 .env，正在创建最终模板..."
    & "$PSScriptRoot\init-final-env.ps1"
    Write-Host "请编辑 .env，至少填写 POSTGRES_PASSWORD、WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD、FRED_API_KEY、DEEPSEEK_API_KEY，然后再次执行 quickstart.ps1。"
    exit 2
}

function Read-DotEnv([string]$name) {
    $line = Get-Content .env | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($null -eq $line) { return "" }
    return $line.Substring($name.Length + 1).Trim()
}

$pg = Read-DotEnv "POSTGRES_PASSWORD"
$admin = Read-DotEnv "WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD"
if ([string]::IsNullOrWhiteSpace($pg) -or $pg -in @("change-me", "widegold")) {
    throw "请先在 .env 设置安全的 POSTGRES_PASSWORD。"
}
if ([string]::IsNullOrWhiteSpace($admin) -or $admin.Length -lt 8) {
    throw "请先在 .env 设置至少 8 位的 WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD。"
}

Write-Host "[1/3] 启动 WideGold Docker Compose..."
& "$PSScriptRoot\docker-up.ps1"

Write-Host "[2/3] 等待 API..."
$httpPort = Read-DotEnv "HTTP_PORT"
if (-not $httpPort) { $httpPort = "8080" }
$apiBase = "http://localhost:$httpPort"
$deadline = (Get-Date).AddSeconds(120)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$apiBase/health" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    docker compose ps
    throw "API 在 120 秒内未就绪，请查看 docker compose logs api / prefect-worker。"
}

Write-Host "[3/3] 启动完成。"
Write-Host "WideGold: $apiBase"
Write-Host "Prefect : http://localhost:$(if (Read-DotEnv 'PREFECT_PORT') { Read-DotEnv 'PREFECT_PORT' } else { '4200' })"
Write-Host "Bridge  : http://localhost:$(if (Read-DotEnv 'EXTERNAL_BRIDGE_PORT') { Read-DotEnv 'EXTERNAL_BRIDGE_PORT' } else { '9100' })/health"
Write-Host "下一步可执行：.\scripts\quicktest.ps1"
