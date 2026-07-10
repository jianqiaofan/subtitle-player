@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python 3。请先安装 Python 3.10 或更高版本。
    pause
    exit /b 1
)
for /f "delims=" %%V in ('py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
echo 目标 Python: %PY_VERSION%
echo.
echo 正在升级 pip...
py -3 -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
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
echo.
echo 安装完成（Python %PY_VERSION%）。
pause
