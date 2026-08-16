@echo off
chcp 65001 >nul

REM Prefer a concrete 3.x that already has project deps (PyQt6).
REM Default "py -3" may point at a newer install without packages.
set "SUBTITLE_PYTHON="
for %%V in (3.10 3.11 3.12 3.13 3.14 3) do (
    if not defined SUBTITLE_PYTHON (
        py -%%V -c "import PyQt6" >nul 2>&1
        if not errorlevel 1 set "SUBTITLE_PYTHON=py -%%V"
    )
)

if not defined SUBTITLE_PYTHON (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        echo [错误] 未找到 Python 3。请安装 Python 3.10 或更高版本。
        exit /b 1
    )
    for /f "delims=" %%V in ('py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
    echo.
    echo [错误] 当前已安装的 Python 均未找到本项目依赖（缺少 PyQt6）。
    echo 请先双击运行「安装依赖.bat」完成安装。
    echo 检测到默认解释器版本：%PY_VERSION%
    echo.
    exit /b 1
)

for /f "delims=" %%V in ('%SUBTITLE_PYTHON% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do set "PY_VERSION=%%V"
echo 使用 Python %PY_VERSION% （%SUBTITLE_PYTHON%）

%SUBTITLE_PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 需要 Python 3.10 或更高版本，当前为 %PY_VERSION%。
    exit /b 1
)

exit /b 0
