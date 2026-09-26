@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.10 -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo 正在安装 PyInstaller...
    py -3.10 -m pip install pyinstaller
)
py -3.10 packaging\build_onedir.py
if errorlevel 1 (
    echo 打包失败。
    pause
    exit /b 1
)
echo.
echo 安装包目录：%cd%\dist\字幕播放器
pause
