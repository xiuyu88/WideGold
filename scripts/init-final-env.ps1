param([switch]$Force)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ((Test-Path ".env") -and -not $Force) {
    throw ".env already exists. Use -Force only if you intentionally want to replace it."
}
Copy-Item ".env.final.example" ".env" -Force
$key = python -c "import secrets; print(secrets.token_urlsafe(32))"
$content = Get-Content ".env" -Raw
$content = $content -replace '(?m)^WIDEGOLD_EXTERNAL_INDICATOR_API_KEY=.*$', "WIDEGOLD_EXTERNAL_INDICATOR_API_KEY=$key"
[System.IO.File]::WriteAllText((Join-Path $root ".env"), $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Created .env with a random internal External Bridge credential."
Write-Host "Now set POSTGRES_PASSWORD, WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD, FRED_API_KEY and DEEPSEEK_API_KEY."
