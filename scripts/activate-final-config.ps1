param(
    [string]$ApiBase = "http://localhost:8080"
)
$ErrorActionPreference = "Stop"
function Read-DotEnv([string]$name) {
    $line = Get-Content .env | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($null -eq $line) { return "" }
    return $line.Substring($name.Length + 1)
}
$user = Read-DotEnv "WIDEGOLD_BOOTSTRAP_ADMIN_USERNAME"
if (-not $user) { $user = "admin" }
$pass = Read-DotEnv "WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD"
$loginBody = @{username=$user; password=$pass} | ConvertTo-Json
$login = Invoke-WebRequest -Uri "$ApiBase/api/v1/auth/login" -Method POST -ContentType "application/json" -Body $loginBody -SessionVariable wg
if ($login.StatusCode -ne 200) { throw "Admin login failed" }
$targets = @(
    @{config_type="INDICATOR_REGISTRY"; version="1.6.0"},
    @{config_type="MODEL_ROUTING"; version="1.1.0"},
    @{config_type="NEWS_COLLECTION"; version="1.2.0"}
)
foreach ($target in $targets) {
    $body = $target | ConvertTo-Json
    try {
        $r = Invoke-WebRequest -Uri "$ApiBase/api/v1/admin/config/activate" -Method POST -ContentType "application/json" -Body $body -WebSession $wg
        Write-Host "Activated $($target.config_type) $($target.version) [$($r.StatusCode)]"
    } catch {
        Write-Warning "Could not activate $($target.config_type) $($target.version): $($_.Exception.Message)"
    }
}
