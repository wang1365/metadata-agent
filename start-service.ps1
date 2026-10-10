$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    uv run python -m metadata_service
} finally {
    Pop-Location
}
