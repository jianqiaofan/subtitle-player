@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_check_python.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
py -3 main.py
pause
