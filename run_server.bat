@echo off
rem ============================================================
rem sync_demo server launcher
rem Run the authoritative server (mode: state / lockstep switchable)
rem ============================================================
setlocal

set SYNC_DEMO_ROLE=server
if "%SYNC_DEMO_PORT%"=="" set SYNC_DEMO_PORT=7808

python -m sync_demo.server
if errorlevel 1 (
    echo.
    echo [ERROR] server exited with an error. Check the message above.
    pause
)
endlocal
