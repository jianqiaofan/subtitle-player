@echo off
setlocal
chcp 65001 >nul

py -3 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python 3。请安装 Python 3.10 或更高版本（当前推荐 3.14）。
    exit /b 1
)

for /f "delims=" %%V in ('py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
echo 使用 Python %PY_VERSION%

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 需要 Python 3.10 或更高版本，当前为 %PY_VERSION%。
    exit /b 1
)

py -3 -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 当前 Python %PY_VERSION% 尚未安装本项目依赖（缺少 PyQt6）。
    echo 请先双击运行「安装依赖.bat」完成安装。
    echo.
    exit /b 1
)

exit /b 0
