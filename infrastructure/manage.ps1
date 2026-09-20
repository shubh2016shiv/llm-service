$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run python -m infrastructure @args
    }
    else {
        & python -m infrastructure @args
    }
    $commandExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $commandExitCode
