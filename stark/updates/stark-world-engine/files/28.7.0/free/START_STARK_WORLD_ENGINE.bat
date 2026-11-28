@echo off
REM ============================================================================
REM  Stark World Engine - double-click me to start.
REM  1. Finds Python 3.10+ (this folder's runtime\python, the "py" launcher, python, python3).
REM  2. If none is found, offers to fetch the official Python 3.12.10 from python.org into
REM     runtime\python (SHA-256 checked, no admin rights, nothing else on the PC changes).
REM  3. Starts studio_app.py; it opens your browser at http://127.0.0.1:<port>/ by itself.
REM  Everything it does is written to launcher.log in this folder.
REM  Optional switches (environment): GS_AUTO_YES=1 skip the download question,
REM  GS_NO_BROWSER=1 don't open a browser, GS_NO_PAUSE=1 don't wait for a key at the end.
REM
REM  Path safety: the folder name may contain ampersand, caret, percent, bang, brackets, quotes
REM  or non-ASCII letters. The folder is read ONCE (delayed expansion still off, so a bang
REM  survives) into APPDIR; after that every path is only used through delayed expansion
REM  (bang-APPDIR-bang), whose value is never parsed again, so no character in it can act as a
REM  command operator. No path is ever passed as an argument to "call" (call expands percent signs a
REM  second time and doubles carets); the subroutines read environment variables instead.
REM ============================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Stark World Engine
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "LOG=%~dp0launcher.log"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PYCMD="
set "PROBE="
set "MSG="
set "PYTHONHOME="
set "PYTHONPATH="
setlocal EnableDelayedExpansion

set "MSG===== launcher start (folder !APPDIR!)"
call :log
echo.
echo   ===============================================
echo         S T A R K   W O R L D   E N G I N E
echo   ===============================================
echo.

if exist "!APPDIR!studio_app.py" goto :findpython
echo   Stark World Engine needs to be unzipped before it can start.
echo.
echo   Right-click the .zip file you downloaded, choose "Extract All...",
echo   then open the extracted folder and double-click START_STARK_WORLD_ENGINE.bat there.
set "MSG=studio_app.py missing - probably run from inside the zip"
call :log
goto :end

:findpython
echo   Looking for Python...
set "RTPY=!APPDIR!runtime\python\python.exe"
if not exist "!RTPY!" goto :trysystem
set "PROBE="!RTPY!""
call :probe
if defined PYCMD goto :found
set "MSG=bundled runtime\python\python.exe did not pass the version check"
call :log
:trysystem
set "PROBE=py -3"
call :probe
if defined PYCMD goto :found
set "PROBE=python"
call :probe
if defined PYCMD goto :found
set "PROBE=python3"
call :probe
if defined PYCMD goto :found
set "MSG=no Python 3.10+ found"
call :log
goto :nopython

:found
set "PYV="
set "VFILE=!TEMP!\stark_pyver_!RANDOM!.txt"
!PYCMD! -c "import sys;print(sys.version.split()[0])" >"!VFILE!" 2>nul
if exist "!VFILE!" set /p PYV=<"!VFILE!"
del "!VFILE!" >nul 2>&1
set "MSG=using Python !PYV!: !PYCMD!"
call :log
echo   Found Python !PYV!.
echo.
REM ---- setup doctor: 0 start, 2 blocker shown, 3 already running (browser opened), 4 copied + started elsewhere.
REM      Any other code means the doctor itself broke: start anyway.
if not exist "!APPDIR!doctor.py" goto :startstudio
echo   Checking your setup...
!PYCMD! -u "!APPDIR!doctor.py" --launch
set "DRC=!ERRORLEVEL!"
set "MSG=doctor exit code !DRC! (details in doctor.log)"
call :log
echo.
if "!DRC!"=="2" goto :end
if "!DRC!"=="3" goto :running
if "!DRC!"=="4" goto :end
goto :startstudio
:running
echo   Stark World Engine is already running, so this window is not needed.
goto :end
:startstudio
REM ---- in-app updates (opt-in): finish or undo a staged update BEFORE the app starts. Silent when nothing is pending.
if exist "!APPDIR!stark_update\updater.py" (
    !PYCMD! -u "!APPDIR!stark_update\updater.py" boot-check 2>>"!LOG!"
)
echo   Starting Stark World Engine... your browser will open in a moment.
echo   Keep this window open while you create. Close it (or press Ctrl+C) to stop.
echo.
set "ARGS="
if "!GS_NO_BROWSER!"=="1" set "ARGS=--no-browser"
!PYCMD! -u "!APPDIR!studio_app.py" !ARGS! 2>>"!LOG!"
set "RC=!ERRORLEVEL!"
set "MSG=studio_app.py exited with code !RC!"
call :log
if "!RC!"=="0" goto :end
echo.
echo   The studio stopped unexpectedly. The last lines of launcher.log say:
echo   ---------------------------------------------------------------
"!PS!" -NoProfile -Command "Get-Content -LiteralPath $env:LOG -Tail 8 | ForEach-Object { '   ' + $_ }" 2>nul
echo   ---------------------------------------------------------------
echo   Closing other Stark World Engine windows and trying again usually fixes it.
goto :end

:nopython
echo.
echo   Stark World Engine runs on Python, a free and safe program. This PC doesn't have it yet.
echo.
if /i "!PROCESSOR_ARCHITECTURE!"=="x86" if not defined PROCESSOR_ARCHITEW6432 goto :manual
if not exist "!PS!" goto :manual
if not exist "!APPDIR!runtime\get_python.ps1" goto :manual
echo   I can fetch the official Python 3.12.10 from python.org (about 11 MB) and keep it
echo   inside this folder only. No admin rights needed, nothing else on your PC changes.
echo.
if "!GS_AUTO_YES!"=="1" goto :fetch
"!SystemRoot!\System32\choice.exe" /c YN /n /m "  Press Y to download it now, or N to cancel: "
if errorlevel 2 goto :manual
:fetch
set "MSG=fetching embeddable Python"
call :log
echo.
REM Paths reach get_python.ps1 through the environment (GS_PY_DEST, GS_PY_LOG). It never deletes a
REM runtime\python whose python.exe works, and replaces a broken one only after the new one is verified.
set "GS_PY_DEST=!APPDIR!runtime\python"
set "GS_PY_LOG=!LOG!"
"!PS!" -NoProfile -ExecutionPolicy Bypass -File "!APPDIR!runtime\get_python.ps1"
if errorlevel 1 goto :fetchfailed
echo.
set "PYCMD="
if not exist "!RTPY!" goto :fetchfailed
set "PROBE="!RTPY!""
call :probe
if defined PYCMD goto :found
:fetchfailed
set "MSG=embeddable Python setup failed"
call :log
echo.
echo   No problem - you can also install Python yourself in two minutes:
:manual
echo.
echo   1. Open https://www.python.org/downloads/ and click the big yellow Download button.
echo   2. Run the installer and tick "Add python.exe to PATH" on the first screen.
echo   3. Double-click START_STARK_WORLD_ENGINE.bat again.
set "MSG=showed manual install instructions"
call :log
if "!GS_NO_PAUSE!"=="1" goto :end
echo.
"!SystemRoot!\System32\choice.exe" /c YN /n /m "  Open python.org in your browser now? (Y/N): "
if errorlevel 2 goto :end
start "" "https://www.python.org/downloads/"
goto :end

:end
set "MSG===== launcher end"
call :log
if "!GS_NO_PAUSE!"=="1" exit /b 0
echo.
pause
exit /b 0

REM ---- :probe  runs the command held in PROBE and sets PYCMD to it when it is Python 3.10 or newer.
REM      It takes no arguments on purpose (see "Path safety" at the top).
:probe
!PROBE! -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYCMD=!PROBE!"
exit /b 0

REM ---- :log  appends MSG to launcher.log (silent when launcher.log is locked, e.g. by a running studio) ----
:log
2>nul (call :logline)
exit /b 0
:logline
>>"!LOG!" echo([!DATE! !TIME!] !MSG!
exit /b 0
