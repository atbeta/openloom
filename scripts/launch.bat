@echo off
title OpenLoom Launcher
echo ======================
echo   OpenLoom Launcher
echo ======================
echo.

:: ── Check OpenCode ──────────────────────────
echo [*] Checking OpenCode (http://127.0.0.1:4096)...
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:4096/health 2>nul | find "200" >nul
if %errorlevel%==0 (
    echo [✓] OpenCode is running
    goto :start
)

echo [!] OpenCode not reachable, trying to start...
start "OpenCode Server" cmd /c "opencode serve"
echo [*] Waiting for OpenCode to come up...

:: Wait up to 30 seconds for OpenCode
set tries=0
:wait
timeout /t 2 /nobreak >nul
set /a tries+=1
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:4096/health 2>nul | find "200" >nul
if %errorlevel%==0 (
    echo [✓] OpenCode responded after %tries% attempts
    goto :start
)
if %tries% lss 15 goto :wait

echo [X] OpenCode still not reachable after 30 seconds
echo     Please start opencode serve manually and re-run.
pause
exit /b 1

:: ── Start OpenLoom ──────────────────────────
:start
echo.
echo [*] Starting OpenLoom...
echo ──────────────────────────────────
openloom serve

:: If openloom exits, pause so the user can read errors
pause
