$ErrorActionPreference = "Stop"

# Accept both PowerShell-style and argparse-style spellings.
$normalizedArgs = @()
foreach ($arg in $args) {
    switch ($arg) {
        "-RequireDocker" { $normalizedArgs += "--require-docker" }
        "-SkipTests"     { $normalizedArgs += "--skip-tests" }
        default          { $normalizedArgs += $arg }
    }
}

python scripts/release_gate.py @normalizedArgs
exit $LASTEXITCODE
