# build-exe.ps1 — Build openloom.exe with PyInstaller (Windows)
# Usage: cd openloom-repo && powershell -File scripts/build-exe.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "[1/3] Syncing dependencies..." -ForegroundColor Cyan
uv sync --extra ui --extra docx --group dev
uv add --dev pyinstaller

Write-Host "[2/3] Resolving UI path..." -ForegroundColor Cyan
$uiPath = uv run python -c "import openloom.server.ui; from pathlib import Path; print(Path(openloom.server.ui.__file__).parent)"
$openloomPath = uv run python -c "import openloom; print(openloom.__file__)"

Write-Host "  UI dir : $uiPath"
Write-Host "  Entry  : $openloomPath"

Write-Host "[3/3] Building exe..." -ForegroundColor Cyan
uv run pyinstaller --onefile --name openloom `
  --add-data "${uiPath};openloom/server/ui" `
  $openloomPath

Write-Host ""
Write-Host "Done! dist/openloom.exe" -ForegroundColor Green
