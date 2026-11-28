@echo off
rem Stark Quill launcher: bundled runtime first, else Python 3.10+ from the PC. The updater runs first so a
rem broken update is rolled back even if the app itself cannot start.
setlocal EnableExtensions DisableDelayedExpansion
set "APP=%~dp0"
setlocal EnableDelayedExpansion
cd /d "!APP!"
set "PY="
if exist "!APP!runtime\python\python.exe" set "PY=!APP!runtime\python\python.exe"
if not defined PY for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
if not defined PY for /f "usebackq delims=" %%P in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
if not defined PY goto nopy
"!PY!" -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>nul || goto nopy
"!PY!" "!APP!auto_updater.py" launch --app-dir "!APP!." -- "!APP!writer_app.py"
exit /b %errorlevel%
:nopy
echo Stark Quill needs Python 3.10 or newer, or the "windows_full" download which includes it.
echo Get Python from https://www.python.org/downloads/ and run this again.
pause
exit /b 9009