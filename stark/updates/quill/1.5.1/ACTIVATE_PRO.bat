@echo off
rem Stark Quill: turn on Pro with a key, no Python needed. The easiest way is inside Quill itself: click
rem "Free plan" at the bottom left, paste the key and press Activate. This file does the same from a double-click,
rem using the Python that comes with Quill (the "windows_full" download), or Python from this PC if there is one.
setlocal EnableExtensions DisableDelayedExpansion
set "APP=%~dp0"
setlocal EnableDelayedExpansion
cd /d "!APP!"
set "PY="
if exist "!APP!runtime\python\python.exe" set "PY=!APP!runtime\python\python.exe"
if not defined PY if exist "!LOCALAPPDATA!\StarkQuill\runtime\python\python.exe" set "PY=!LOCALAPPDATA!\StarkQuill\runtime\python\python.exe"
if not defined PY for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
if not defined PY for /f "usebackq delims=" %%P in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
if not defined PY goto inapp
"!PY!" -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>nul || goto inapp
echo.
echo   Stark Quill: turn on Pro
echo.
set "KEY=%~1"
if not defined KEY set /p "KEY=  Paste your key (one line that starts with GQ1.) and press Enter: "
if not defined KEY goto nokey
"!PY!" "!APP!license_guard.py" activate "!KEY!"
set "RC=!errorlevel!"
echo.
if "!RC!"=="0" (echo   Done. Start Stark Quill as usual.) else (echo   That did not work. Check the key, or open Quill and use Free plan, then Activate.)
if not defined QUILL_NO_PAUSE pause
exit /b !RC!
:nokey
echo   No key was pasted, so nothing changed.
if not defined QUILL_NO_PAUSE pause
exit /b 2
:inapp
echo.
echo   This computer has no Python for this file to use, and that is fine:
echo   open Stark Quill, click "Free plan" at the bottom left, paste your key and press Activate.
echo   (The "windows_full" download includes everything Quill needs.)
echo.
if not defined QUILL_NO_PAUSE pause
exit /b 9009
