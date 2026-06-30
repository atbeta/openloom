# OpenLoom Launcher — PowerShell
# Checks for OpenCode, starts it if needed, then launches OpenLoom.

Write-Host "======================" -ForegroundColor Cyan
Write-Host "  OpenLoom Launcher" -ForegroundColor Cyan
Write-Host "======================" -ForegroundColor Cyan
Write-Host ""

# ── Check OpenCode ──────────────────────────
$opencodeUrl = "http://127.0.0.1:4096"
Write-Host "[*] Checking OpenCode ($opencodeUrl)..." -ForegroundColor Gray

try {
    $response = Invoke-WebRequest -Uri "$opencodeUrl/health" -TimeoutSec 3 -SkipCertificateCheck
    if ($response.StatusCode -eq 200) {
        Write-Host "[✓] OpenCode is running" -ForegroundColor Green
    }
} catch {
    Write-Host "[!] OpenCode not reachable, trying to start..." -ForegroundColor Yellow
    Start-Process -FilePath "opencode" -ArgumentList "serve" -WindowStyle Minimized

    # Wait up to 30 seconds
    $tries = 0
    do {
        Start-Sleep -Seconds 2
        $tries++
        try {
            $check = Invoke-WebRequest -Uri "$opencodeUrl/health" -TimeoutSec 3 -SkipCertificateCheck
            if ($check.StatusCode -eq 200) {
                Write-Host "[✓] OpenCode responded after $tries attempts" -ForegroundColor Green
                break
            }
        } catch { }
    } while ($tries -lt 15)

    if ($tries -ge 15) {
        Write-Host "[X] OpenCode still not reachable after 30 seconds" -ForegroundColor Red
        Write-Host "    Please start 'opencode serve' manually and re-run."
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── Start OpenLoom ──────────────────────────
Write-Host ""
Write-Host "[*] Starting OpenLoom..." -ForegroundColor Gray
Write-Host "──────────────────────────────────" -ForegroundColor DarkGray

$openloomExe = Get-Command openloom -ErrorAction SilentlyContinue
if (-not $openloomExe) {
    Write-Host "[X] 'openloom' not found in PATH" -ForegroundColor Red
    Write-Host "    Install: uv tool install 'openloom[ui]'"
    Read-Host "Press Enter to exit"
    exit 1
}

& openloom serve @args

Write-Host ""
Write-Host "OpenLoom exited." -ForegroundColor Yellow
Read-Host "Press Enter to exit"
