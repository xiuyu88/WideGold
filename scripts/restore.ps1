param([Parameter(Mandatory=$true)][string]$InputPath)

$ErrorActionPreference = "Stop"
$fullPath = [System.IO.Path]::GetFullPath($InputPath)
if (!(Test-Path $fullPath)) { throw "Backup not found: $fullPath" }
$user = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "widegold" }
$db = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "widegold" }
Write-Host "[WideGold] WARNING: restoring $fullPath into $db"

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = "docker"
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardError = $true
foreach ($arg in @("compose", "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", $user, "-d", $db)) {
    [void]$psi.ArgumentList.Add($arg)
}
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $psi
if (-not $process.Start()) { throw "Unable to start docker psql" }

$file = [System.IO.File]::OpenRead($fullPath)
try {
    $file.CopyTo($process.StandardInput.BaseStream)
    $process.StandardInput.BaseStream.Flush()
} finally {
    $file.Dispose()
    $process.StandardInput.Close()
}
$stderr = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "psql restore failed (exit $($process.ExitCode)): $stderr" }
Write-Host "[WideGold] restore complete"
