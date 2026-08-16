@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Prefer Python that already has deps, else first available 3.10+.
set "PYTHON_CMD="
for %%V in (3.10 3.11 3.12 3.13 3.14 3) do (
    if not defined PYTHON_CMD (
        py -%%V -c "import PyQt6" >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=py -%%V"
    )
)
if not defined PYTHON_CMD (
    for %%V in (3.10 3.11 3.12 3.13 3.14 3) do (
        if not defined PYTHON_CMD (
            py -%%V --version >nul 2>&1
            if not errorlevel 1 set "PYTHON_CMD=py -%%V"
        )
    )
)
if not defined PYTHON_CMD (
    echo [错误] 未找到 Python 3。请先安装 Python 3.10 或更高版本。
    pause
    exit /b 1
)

for /f "delims=" %%V in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
echo 目标 Python: %PY_VERSION% （%PYTHON_CMD%）
echo.
echo 正在升级 pip...
%PYTHON_CMD% -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo 正在安装依赖（使用清华镜像）...
%PYTHON_CMD% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
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
