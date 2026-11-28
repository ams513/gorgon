@echo off
rem Stark Covenant installer - current user only, no admin rights needed.
rem  * copies the app to %LOCALAPPDATA%\GorgonCovenant (never deletes anything there; the vault folder is untouched)
rem  * creates Documents\Stark_Covenant_Data if missing (existing data is never touched). An install from before
rem    1.1.0 keeps Documents\Gorgon_Covenant_Data: the app copies it to the Stark name at its next start, checks
rem    every file, then switches; the old folder is never deleted.
rem  * creates a Desktop shortcut "Stark Covenant" (replacing an old "Gorgon Covenant" one) and starts the app
rem  * the program folder keeps its pre-1.1.0 name %LOCALAPPDATA%\GorgonCovenant on purpose: the vault and the
rem    licence live under it, and existing installs and their updates must keep finding them.
rem Special-character safety: every path is captured while delayed expansion is OFF and only used as "!VAR!"
rem afterwards, so spaces, & ( ) ^ %% ! and ' in user/folder names are safe. This script takes no arguments.
rem Test overrides (optional): GC_DESKTOP (shortcut folder), GC_DATA_DIR (data folder), GC_NO_LAUNCH=1.
rem NOT a certified EHR / not a medical device / no HIPAA or GDPR claim: see README.md before real patient data.
setlocal EnableExtensions DisableDelayedExpansion
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\GorgonCovenant"
set "DOCS="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[Environment]::GetFolderPath('MyDocuments')"`) do set "DOCS=%%D"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "DATA=%DOCS%\Stark_Covenant_Data"
set "OLDDATA=%DOCS%\Gorgon_Covenant_Data"
set "GC_DATA_ARG="
if defined GC_DATA_DIR set "DATA=%GC_DATA_DIR%"
if defined GC_DATA_DIR set "GC_DATA_ARG=1"
set "PY="
if exist "%SRC%runtime\python\python.exe" set "PY=%SRC%runtime\python\python.exe"
if not defined PY for /f "usebackq delims=" %%P in (`py -3 -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
if not defined PY for /f "usebackq delims=" %%P in (`python -c "import sys;print(sys.executable)" 2^>nul`) do set "PY=%%P"
setlocal EnableDelayedExpansion

if not defined LOCALAPPDATA (
    echo Cannot find your local application data folder. Nothing was changed.
    goto fail
)
if not exist "!SRC!clinic_app.py" (
    echo This installer must sit inside the unzipped Stark Covenant folder ^(clinic_app.py not found next to it^).
    goto fail
)
if not defined PY goto nopy
"!PY!" -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>nul || goto nopy

set "GC_VER="
if exist "!SRC!VERSION" set /p GC_VER=<"!SRC!VERSION"
echo Stark Covenant !GC_VER! - a Stark product
echo Installing Stark Covenant for this user...
if not exist "!DEST!\" mkdir "!DEST!" || goto fail
rem /E copies subfolders; no /MIR or /PURGE, so nothing already in the install folder is ever deleted.
rem Seller-only and development files are excluded.
robocopy "!SRC!." "!DEST!" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ .keys .cache .git dist tests vault /XF *.pyc keygen_chart.py deploy_chart.ps1 publish_chart.json .gitignore >nul
if errorlevel 8 (
    echo Copy failed. Close Stark Covenant if it is running and try again.
    goto fail
)

if exist "!DATA!\" (
    echo Your data folder is already there and was left exactly as it is: !DATA!
) else if not defined GC_DATA_ARG if exist "!OLDDATA!\" (
    echo Your data folder !OLDDATA! was left exactly as it is. Stark Covenant copies it to !DATA!
    echo at its next start, checks every file, and keeps the old folder.
    set "DATA=!OLDDATA!"
)
if not exist "!DATA!\" (
    mkdir "!DATA!" || goto fail
    echo Created your data folder: !DATA!
)

rem Shortcut target: the packaged launcher (bundled runtime + safe updates) if present, else Python directly.
rem Without --data the app picks its own folder (and copies a pre-1.1.0 one); --data only for an explicit choice.
set "GC_LAUNCHER="
if exist "!DEST!\START_GORGON_COVENANT.bat" set "GC_LAUNCHER=!DEST!\START_GORGON_COVENANT.bat"
if exist "!DEST!\START_STARK_COVENANT.bat" set "GC_LAUNCHER=!DEST!\START_STARK_COVENANT.bat"
if defined GC_LAUNCHER (
    set "GC_LNK_TARGET=!GC_LAUNCHER!"
    set "GC_LNK_ARGS="
) else (
    set "GC_LNK_TARGET=!PY!"
    set "GC_LNK_ARGS="!DEST!\clinic_app.py""
    if defined GC_DATA_ARG set "GC_LNK_ARGS="!DEST!\clinic_app.py" --data "!DATA!""
)
set "GC_LNK_DIR=!DEST!"
set "GC_LNK_ICON="
if exist "!DEST!\assets\gorgon-covenant.ico" set "GC_LNK_ICON=!DEST!\assets\gorgon-covenant.ico"
if exist "!DEST!\assets\stark-covenant.ico" set "GC_LNK_ICON=!DEST!\assets\stark-covenant.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=$env:GC_DESKTOP; if(-not $d){$d=[Environment]::GetFolderPath('Desktop')}; $old=Join-Path $d 'Gorgon Covenant.lnk'; if(Test-Path -LiteralPath $old){Remove-Item -LiteralPath $old -Force}; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Stark Covenant.lnk')); $s.TargetPath=$env:GC_LNK_TARGET; $s.Arguments=$env:GC_LNK_ARGS; $s.WorkingDirectory=$env:GC_LNK_DIR; $s.WindowStyle=7; if($env:GC_LNK_ICON){$s.IconLocation=$env:GC_LNK_ICON+',0'}; $s.Description='Stark Covenant - local clinic records (not a certified EHR)'; $s.Save()" >nul 2>nul
if errorlevel 1 (
    echo Note: the Desktop shortcut could not be created. Start Stark Covenant from !DEST! instead.
) else (
    echo Desktop shortcut created: Stark Covenant
)

echo.
if exist "!DEST!\CHANNEL" (
    set /p GC_CH=<"!DEST!\CHANNEL"
    if /i "!GC_CH!"=="beta" echo BETA: evaluate with DEMO data first.
)
echo Stark Covenant is NOT a certified EHR and NOT a medical device, and makes no HIPAA/GDPR compliance claim.
echo Complete your own compliance review before entering real patient data. See README.md.
if "!GC_NO_LAUNCH!"=="1" (
    echo Installed to !DEST!
    exit /b 0
)
echo Installed. Starting Stark Covenant...
if defined GC_LAUNCHER (
    start "Stark Covenant" /MIN /D "!DEST!" "!GC_LAUNCHER!"
) else if defined GC_DATA_ARG (
    start "Stark Covenant" /MIN /D "!DEST!" "!PY!" "!DEST!\clinic_app.py" --data "!DATA!"
) else (
    start "Stark Covenant" /MIN /D "!DEST!" "!PY!" "!DEST!\clinic_app.py"
)
endlocal & endlocal
exit /b 0

:nopy
echo Stark Covenant needs Python 3.10 or newer ^(or the "windows_full" download, which includes it^).
echo Get Python from https://www.python.org/downloads/ and run this installer again. Nothing was changed.
goto fail

:fail
echo Installation did not finish.
if not "!GC_NO_LAUNCH!"=="1" pause
exit /b 1
