@echo off
rem One-click launcher for the glasses simulator.
rem Backend check + package check + WSLg window, all in one.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-sim.ps1" %*
endlocal
