# OpenLoom Launcher — PowerShell
# Checks OpenCode is reachable, then launches OpenLoom.
# Does NOT try to auto-start OpenCode — it can't guess the port.

$ErrorActionPreference = "Continue"

$opencodeUrl = $env:OPENLOOM_OPENCODE_URL
if (-not $opencodeUrl) { $opencodeUrl = "http://127.0.0.1:4096" }

Write-Host "OpenLoom Launcher" -ForegroundColor Cyan
Write-Host "  OpenCode: $opencodeUrl" -ForegroundColor DarkGray
Write-Host ""

# Simple TCP check — don't assume any specific path
Write-Host "[*] Checking OpenCode..." -ForegroundColor Gray
$reachable = $false
try {
    $null = Invoke-WebRequest -Uri $opencodeUrl -TimeoutSec 3 -SkipCertificateCheck
    $reachable = $true
} catch {
    # 2xx/3xx/4xx all mean "it's running" — only total failure means "not reachable"
    if ($_.Exception.Response) {
        $reachable = $true
    }
}

if ($reachable) {
    Write-Host "[✓] OpenCode reachable" -ForegroundColor Green
    Write-Host ""

    $openloomExe = Get-Command openloom -ErrorAction SilentlyContinue
    if (-not $openloomExe) {
        Write-Host "[X] 'openloom' not found in PATH" -ForegroundColor Red
        Write-Host "    Install: uv tool install 'openloom[ui,docx]'"
        Read-Host "Press Enter to exit"
        exit 1
    }

    & openloom serve @args
    Write-Host ""
    Write-Host "OpenLoom exited." -ForegroundColor Yellow
    exit 0
}

Write-Host "[!] OpenCode not reachable at $opencodeUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "    Start it with:  opencode serve"
Write-Host "    Or set:         `$env:OPENLOOM_OPENCODE_URL = 'http://YOUR_HOST:PORT'"
Write-Host "    Then re-run this script."
Write-Host ""
Read-Host "Press Enter to exit"
exit 1
