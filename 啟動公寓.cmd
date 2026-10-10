@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README.md to install Python and dependencies first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m our_little_days
if errorlevel 1 pause
