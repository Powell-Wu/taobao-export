@echo off
setlocal
rem ============================================================
rem  Taobao Export Tool - portable Windows launcher
rem  First run: create .venv and install requirements automatically.
rem  Keep this window OPEN. Type q + Enter or close it to stop.
rem  ASCII only: avoids cmd.exe encoding problems on Chinese Windows.
rem ============================================================
chcp 65001 >nul
title Taobao Export Tool - RUNNING (q = stop)
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" goto run

set "BOOTSTRAP="
where py >nul 2>&1 && set "BOOTSTRAP=py -3"
if not defined BOOTSTRAP (
  where python >nul 2>&1 && set "BOOTSTRAP=python"
)
if not defined BOOTSTRAP (
  echo Python 3.10 or newer is required.
  echo Install it from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

echo First run: creating local Python environment...
%BOOTSTRAP% -m venv ".venv"
if errorlevel 1 goto fail

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto fail

:run
".venv\Scripts\python.exe" -u "app.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Service stopped. Press any key to close this window.
pause >nul
exit /b %EXIT_CODE%

:fail
echo.
echo Setup failed. Check your Python installation and network, then try again.
pause
exit /b 1
