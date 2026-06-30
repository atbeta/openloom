@echo off
title OpenLoom Launcher
echo ======================
echo   OpenLoom Launcher
echo ======================
echo.

:: ── Check OpenCode ──────────────────────────
set OPENCODE_URL=%OPENLOOM_OPENCODE_URL%
if "%OPENCODE_URL%"=="" set OPENCODE_URL=http://127.0.0.1:4096

echo [*] Checking OpenCode (%OPENCODE_URL%)...
curl.exe -sS -o NUL -w "%%{http_code}" "%OPENCODE_URL%/global/health" 2>NUL | findstr /r "^2" >NUL
if %errorlevel%==0 (
    echo [✓] OpenCode is running
    goto :start
)

echo [!] OpenCode not reachable, trying to start...
start "" "opencode" serve
echo [*] Waiting for OpenCode...

set tries=0
:wait
timeout /t 2 /nobreak >NUL
set /a tries+=1
curl.exe -sS -o NUL -w "%%{http_code}" "%OPENCODE_URL%/global/health" 2>NUL | findstr /r "^2" >NUL
if %errorlevel%==0 (
    echo [✓] OpenCode responded after %tries% attempts
    goto :start
)
if %tries% lss 15 goto :wait

echo [X] OpenCode still not reachable after 30s
pause
exit /b 1

:: ── Start OpenLoom ──────────────────────────
:start
echo.
echo [*] Starting OpenLoom...
openloom serve %*

if %errorlevel% neq 0 pause
