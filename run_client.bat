@echo off
rem ============================================================
rem sync_demo client launcher
rem Run one client window (open multiple for multiplayer)
rem Optional: set SYNC_DEMO_HOST / SYNC_DEMO_PORT before running.
rem ============================================================
setlocal

set SYNC_DEMO_ROLE=client
if "%SYNC_DEMO_HOST%"=="" set SYNC_DEMO_HOST=127.0.0.1
if "%SYNC_DEMO_PORT%"=="" set SYNC_DEMO_PORT=7808

python -m sync_demo.client
if errorlevel 1 (
    echo.
    echo [ERROR] client exited with an error. Check the message above.
    pause
)
endlocal
