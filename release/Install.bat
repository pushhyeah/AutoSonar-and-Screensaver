@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"
if errorlevel 1 (
    echo.
    echo Installation failed.
) else (
    echo.
    echo AutoSonar is installed.
)
pause
