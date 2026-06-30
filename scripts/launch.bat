@echo off
title OpenLoom Launcher

:: Use OPENLOOM_OPENCODE_URL if set, or OpenLoom's default
set OPENCODE_URL=%OPENLOOM_OPENCODE_URL%
if "%OPENCODE_URL%"=="" set OPENCODE_URL=http://127.0.0.1:4096

echo OpenLoom Launcher
echo   OpenCode: %OPENCODE_URL%
echo.

:: Simple TCP check — just see if the port answers, no path assumptions
echo [*] Checking OpenCode...
curl.exe -sS --max-time 3 "%OPENCODE_URL%" >NUL 2>&1
if %errorlevel%==0 (
    echo [✓] OpenCode reachable
    echo.
    openloom serve %*
    if %errorlevel% neq 0 pause
    exit /b
)

:: Not reachable
echo [!] OpenCode not reachable at %OPENCODE_URL%
echo.
echo     Start it with: opencode serve
echo     Or set:       set OPENLOOM_OPENCODE_URL=http://YOUR_HOST:PORT
echo     Then re-run this script.
echo.
pause
