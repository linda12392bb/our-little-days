@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import sys; assert sys.maxsize.bit_length() == 63" >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 x64 with the Python Launcher, then run this file again.
  echo See docs\WINDOWS_USER_GUIDE.md for the source build instructions.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements-lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-deps --no-build-isolation -e .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\audit_characters.py >nul
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\build_windows.py
if errorlevel 1 goto failed
echo Done. The complete portable ZIP is in the artifacts folder.
echo Real Windows desktop capture still needs manual testing.
start "" explorer.exe "%cd%\artifacts"
pause
exit /b 0
:failed
echo Build failed. Please keep the error message above. Existing apartment data was not reset.
pause
exit /b 1
