@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AutoSonar.ps1"
if errorlevel 1 (
    echo.
    echo Installation failed.
    pause
)
