@echo off
chcp 65001 >nul
cd /d "%~dp0"

call "%~dp0_check_python.bat"
if errorlevel 1 (
    pause
    exit /b 1
)

REM Close the visible console; a hidden cmd keeps running until the GUI exits.
wscript //nologo "%~dp0_hide_console.vbs" "%~dp0_run_hidden.bat"
if errorlevel 1 (
    %SUBTITLE_PYTHON% main.py
    exit /b %ERRORLEVEL%
)
exit /b 0
