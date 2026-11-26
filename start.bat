@echo off
setlocal
cd /d "%~dp0"

if "%PORT%"=="" set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%/"
if "%~1"=="--demo" set "URL=http://127.0.0.1:%PORT%/?demo"

curl -s -o nul "http://127.0.0.1:%PORT%/api/state" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo Gemini photo sorter is already running.
    start "" "%URL%"
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    call setup.bat || exit /b 1
)

if "%~1"=="--demo" (
    if not exist "dashboard\demo\mini\0.jpg" (
        .venv\Scripts\python.exe scripts\demo.py || exit /b 1
    )
)

start "" /b cmd /c "timeout /t 1 /nobreak >nul & start "" "%URL%""
set "PYTHONUTF8=1"
.venv\Scripts\python.exe scripts\server.py
