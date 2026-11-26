@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
    where python >nul 2>&1 && for /f "delims=" %%I in ('where python') do (
        echo %%I | findstr /i "WindowsApps" >nul || (if not defined PY set "PY=%%I")
    )
)
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo Python 3.9+ was not found. Install Python 3.12 with:
    echo   winget install Python.Python.3.12
    exit /b 1
)

where ffmpeg >nul 2>&1 || (
    echo Note: ffmpeg is not installed, so videos will get placeholder previews.
    echo       Optional install: winget install Gyan.FFmpeg
)

echo Creating virtual environment with %PY%...
%PY% -m venv .venv || exit /b 1
.venv\Scripts\python.exe -m pip install -q --upgrade pip || exit /b 1
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || exit /b 1
echo Setup done. Next: start.bat   (or start.bat --demo to see the demo)
