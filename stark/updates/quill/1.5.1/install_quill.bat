@echo off
rem Stark Quill installer: current user only, no admin rights needed.
rem  - copies this package to %LOCALAPPDATA%\StarkQuill. A PC with a copy from before 1.5.1 (the old folder)
rem    and no StarkQuill yet gets the package in that old folder; Quill then copies the folder, settings and
rem    licence included, to StarkQuill on its first start, checks it and moves the shortcut there
rem  - creates %USERPROFILE%\Documents\My_Books if missing (existing books are never touched)
rem  - creates a Desktop shortcut and starts Stark Quill
rem Paths are read with delayed expansion OFF and used as !VAR! inside quotes, so spaces, &, (, ), %, ^ are safe.
setlocal EnableExtensions DisableDelayedExpansion
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\StarkQuill"
if not exist "%DEST%\" if exist "%LOCALAPPDATA%\GorgonQuill\" set "DEST=%LOCALAPPDATA%\GorgonQuill"
set "BOOKS=%USERPROFILE%\Documents\My_Books"
setlocal EnableDelayedExpansion

if not defined LOCALAPPDATA (
    echo Cannot find your local application data folder. Nothing was changed.
    goto fail
)
if not exist "!SRC!writer_app.py" (
    echo This installer must sit inside the unzipped Stark Quill folder ^(writer_app.py not found next to it^).
    goto fail
)

set "GQ_VER="
if exist "!SRC!VERSION" for /f "usebackq delims=" %%V in ("!SRC!VERSION") do set "GQ_VER=%%V"
echo Stark Quill !GQ_VER!, a Stark product
echo Installing for this user...
if not exist "!DEST!\" mkdir "!DEST!" || goto fail
rem /E all subfolders, /R:1 /W:1 short retries. Books never live in this folder.
robocopy "!SRC!." "!DEST!" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ >nul
if errorlevel 8 (
    echo Copy failed. Close Stark Quill if it is running and try again.
    goto fail
)

if not exist "!BOOKS!\" (
    mkdir "!BOOKS!" || goto fail
    echo Created your books folder: !BOOKS!
) else (
    echo Your books folder is already there and was left exactly as it is.
)

set "GQ_LAUNCH=START_STARK_QUILL.bat"
set "GQ_LNK_TARGET=!DEST!\!GQ_LAUNCH!"
set "GQ_LNK_DIR=!DEST!"
set "GQ_LNK_ICON="
if exist "!DEST!\assets\quill.ico" set "GQ_LNK_ICON=!DEST!\assets\quill.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=$env:GQ_DESKTOP; if(-not $d){$d=[Environment]::GetFolderPath('Desktop')}; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Stark Quill.lnk')); $s.TargetPath=$env:GQ_LNK_TARGET; $s.WorkingDirectory=$env:GQ_LNK_DIR; $s.WindowStyle=7; $s.Description='Stark Quill, a Stark product'; if($env:GQ_LNK_ICON){$s.IconLocation=$env:GQ_LNK_ICON+',0'}; $s.Save()" >nul 2>nul
if errorlevel 1 (
    echo Note: the Desktop shortcut could not be created. Start Stark Quill from the install folder instead.
) else (
    echo Desktop shortcut created: Stark Quill
)

rem GQ_NO_LAUNCH=1 (used by the automated installer test) skips starting the app.
if "!GQ_NO_LAUNCH!"=="1" (
    echo Installed to !DEST!
    exit /b 0
)
echo Installed. Starting Stark Quill...
start "Stark Quill" /D "!DEST!" "!DEST!\!GQ_LAUNCH!"
endlocal & endlocal
exit /b 0

:fail
echo Installation did not finish.
pause
exit /b 1
