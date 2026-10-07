$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The project virtual environment was not found. Create it with: py -m venv .venv"
}

& $python -m pip install -r (Join-Path $projectRoot "requirements-build.txt")
& $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "SanfordLibrary.spec")

Write-Host "Built: $projectRoot\dist\Sanford Library.exe"
