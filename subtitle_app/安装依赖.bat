@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装依赖（使用清华镜像）...
py -3 -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo 安装失败，请检查网络或 Python 环境。
    pause
    exit /b 1
)
if not exist config.json (
    echo.
    echo 提示：首次使用请运行「首次配置.bat」创建 config.json
)
echo 安装完成。
pause
