@echo off
rem One-click launcher for the glasses simulator.
rem Backend check + package check + WSLg window, all in one.
rem NOTE: keep this file ASCII-only so cmd.exe never chokes on encoding.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-sim.ps1" %*
if errorlevel 1 (
  echo.
  echo [sim.bat] launch failed - see the message above. Press any key to close.
  pause >nul
)
endlocal
