$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & uv run python main.py @args
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
