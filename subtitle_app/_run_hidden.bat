@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_check_python.bat"
if errorlevel 1 exit /b 1
set "SUBTITLE_HIDE_CONSOLE=1"
if /i "%~1"=="--transcribe" (
    %SUBTITLE_PYTHON% main.py --transcribe
) else (
    %SUBTITLE_PYTHON% main.py
)
exit /b %ERRORLEVEL%
