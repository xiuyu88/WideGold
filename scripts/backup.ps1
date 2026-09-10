param([string]$OutputPath = "")

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = "backups/widegold_$stamp.sql" }
$fullPath = [System.IO.Path]::GetFullPath($OutputPath)
$dir = [System.IO.Path]::GetDirectoryName($fullPath)
if ($dir) { [System.IO.Directory]::CreateDirectory($dir) | Out-Null }

$user = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "widegold" }
$db = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "widegold" }
Write-Host "[WideGold] backing up $db -> $fullPath"

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = "docker"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
foreach ($arg in @("compose", "exec", "-T", "postgres", "pg_dump", "-U", $user, "-d", $db, "--no-owner", "--no-privileges")) {
    [void]$psi.ArgumentList.Add($arg)
}
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $psi
if (-not $process.Start()) { throw "Unable to start docker pg_dump" }

$file = [System.IO.File]::Create($fullPath)
try {
    $process.StandardOutput.BaseStream.CopyTo($file)
} finally {
    $file.Dispose()
}
$stderr = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    Remove-Item -Force -ErrorAction SilentlyContinue $fullPath
    throw "pg_dump failed (exit $($process.ExitCode)): $stderr"
}
Write-Host "[WideGold] backup complete: $fullPath"
