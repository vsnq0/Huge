@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0Huge.exe" (
    start "" "%~dp0Huge.exe"
    endlocal
    exit /b 0
)
py -3 desktop.py
if errorlevel 1 (
    echo.
    echo Huge could not start. Build the UI with npm run build in the ui folder,
    echo then make sure Python 3 and pywebview are installed:
    echo   py -3 -m pip install -r requirements.txt
    pause
)
endlocal
