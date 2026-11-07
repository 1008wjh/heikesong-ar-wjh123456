@echo off
REM MemoryLens launcher -- keep this file ASCII-only so cmd.exe never chokes on encoding.
title MemoryLens
cd /d "%~dp0"

echo ============================================
echo   MemoryLens - local service
echo ============================================
echo.

REM Pick a python that actually runs (the Windows Store stub also answers to "where python").
REM Note: no variables inside the blocks below -- cmd expands %VAR% at parse time there.
set "PY="
for %%C in ("python" "py") do (
  if not defined PY (
    %%~C -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=%%~C"
  )
)
if not defined PY (
  if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
  )
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.10+ or edit PY in this file.
  pause
  exit /b 1
)

echo Using: %PY%
"%PY%" -c "import cv2,onnxruntime,numpy,sherpa_onnx,langgraph" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing dependencies...
  "%PY%" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ opencv-python-headless onnxruntime numpy sherpa-onnx langgraph
)

REM Optional local secrets/config, never committed: copy env.local.bat.example to env.local.bat
if exist "env.local.bat" call "env.local.bat"

REM The repo no longer ships real data/people.json (privacy). Create a demo DB on first run.
if not exist "data\people.json" (
  echo [INFO] Creating demo people database...
  "%PY%" tools\seed_demo.py
)

echo.
echo   Service : http://127.0.0.1:8765
echo   Demo    : http://127.0.0.1:8765/?demo=1
echo   Stop    : Ctrl+C
echo.

if not defined ML_NO_BROWSER start "" http://127.0.0.1:8765
"%PY%" src\server.py
pause
