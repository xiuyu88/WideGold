param([switch]$LiveAnalysis)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/3] External Bridge Smoke（不调用 LLM）"
python scripts/external_bridge_smoke.py
if ($LASTEXITCODE -ne 0) { throw "External Bridge Smoke 失败" }

Write-Host "[2/3] Docker E2E（默认不触发 Live Analysis）"
& "$PSScriptRoot\docker-e2e.ps1"

Write-Host "[3/3] Release Gate"
& "$PSScriptRoot\release-gate.ps1" -RequireDocker
if ($LASTEXITCODE -ne 0) { throw "Release Gate 失败" }

if ($LiveAnalysis) {
    Write-Warning "即将触发真实 Live Analysis，可能消耗 LLM Token。"
    & "$PSScriptRoot\docker-e2e.ps1" -Analysis
}

Write-Host "WideGold 快速验收完成。"
