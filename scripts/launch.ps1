# OpenLoom Launcher — PowerShell
# Checks for OpenCode, starts it if needed, then launches OpenLoom.

$ErrorActionPreference = "Continue"

Write-Host "======================" -ForegroundColor Cyan
Write-Host "  OpenLoom Launcher" -ForegroundColor Cyan
Write-Host "======================" -ForegroundColor Cyan
Write-Host ""

# ── Check OpenCode ──────────────────────────
$opencodeUrl = $env:OPENLOOM_OPENCODE_URL
if (-not $opencodeUrl) { $opencodeUrl = "http://127.0.0.1:4096" }
$healthUrl = "$opencodeUrl/global/health"

Write-Host "[*] Checking OpenCode ($healthUrl)..." -ForegroundColor Gray

$running = $false
try {
    $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -SkipCertificateCheck
    if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
        Write-Host "[✓] OpenCode is running" -ForegroundColor Green
        $running = $true
    }
} catch {
    Write-Host "    ($($_.Exception.Message))" -ForegroundColor DarkGray
}

if (-not $running) {
    Write-Host "[!] OpenCode not reachable, trying to start..." -ForegroundColor Yellow
    Start-Process -FilePath "opencode" -ArgumentList "serve" -WindowStyle Minimized

    Write-Host "[*] Waiting for OpenCode..." -ForegroundColor Gray
    $tries = 0
    do {
        Start-Sleep -Seconds 2
        $tries++
        try {
            $check = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -SkipCertificateCheck
            if ($check.StatusCode -ge 200 -and $check.StatusCode -lt 300) {
                Write-Host "[✓] OpenCode responded after $tries attempts" -ForegroundColor Green
                $running = $true
                break
            }
        } catch { }
    } while ($tries -lt 15)

    if (-not $running) {
        Write-Host "[X] OpenCode still not reachable after 30 seconds" -ForegroundColor Red
        Write-Host "    URL : $opencodeUrl"
        Write-Host "    Test: curl.exe -sS $healthUrl"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── Start OpenLoom ──────────────────────────
Write-Host ""
Write-Host "[*] Starting OpenLoom..." -ForegroundColor Gray

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
