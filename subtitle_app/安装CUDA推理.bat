@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "CUDA_ROOT=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
set "CUDA_HOME="

rem 优先 12.4 / 12.9，其次任意已安装的 12.x
if exist "%CUDA_ROOT%\v12.4\bin\nvcc.exe" (
    set "CUDA_HOME=%CUDA_ROOT%\v12.4"
    echo 使用 CUDA 12.4
) else if exist "%CUDA_ROOT%\v12.9\bin\nvcc.exe" (
    set "CUDA_HOME=%CUDA_ROOT%\v12.9"
    echo 使用 CUDA 12.9
) else if exist "%CUDA_ROOT%" (
    for /f "delims=" %%V in ('dir /b /ad /o-n "%CUDA_ROOT%\v12.*" 2^>nul') do (
        if exist "%CUDA_ROOT%\%%V\bin\nvcc.exe" (
            set "CUDA_HOME=%CUDA_ROOT%\%%V"
            echo 使用 CUDA %%V
            goto :cuda_found
        )
    )
)

:cuda_found
if not defined CUDA_HOME (
    echo.
    echo ========================================
    echo  未找到 CUDA Toolkit
    echo ========================================
    echo.
    echo 说明：显卡驱动 ^(nvidia-smi^) 不等于 CUDA Toolkit。
    echo       编译 GPU 版 pywhispercpp 需要单独安装 CUDA Toolkit。
    echo.
    echo 推荐安装 CUDA 12.4（Windows x86_64）：
    echo   https://developer.nvidia.com/cuda-12-4-0-download-archive
    echo.
    echo 也可安装 CUDA 12.9：
    echo   https://developer.nvidia.com/cuda-downloads
    echo.
    echo 安装完成后默认路径应为：
    echo   %CUDA_ROOT%\v12.4
    echo 然后重新运行本脚本。
    echo.
    pause
    exit /b 1
)

set "VCVARS="
if exist "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat" (
    set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" (
    set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat" (
    set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
    set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)

if not defined VCVARS (
    echo 未找到 Visual Studio 2022 构建环境（需 C++ 桌面开发工作负载）。
    pause
    exit /b 1
)

call "%VCVARS%"
if errorlevel 1 (
    echo 无法加载 Visual Studio 2022 构建环境。
    pause
    exit /b 1
)

set "PATH=%CUDA_HOME%\bin;%PATH%"
set "CUDA_PATH=%CUDA_HOME%"
set "CUDACXX=%CUDA_HOME%\bin\nvcc.exe"
set GGML_CUDA=1
set CMAKE_ARGS=-DCMAKE_CUDA_ARCHITECTURES=89 -DGGML_CUDA_NO_VMM=ON -DCUDAToolkit_ROOT="%CUDA_HOME%"

echo.
echo ========================================
echo  编译 CUDA 版 pywhispercpp
echo  显卡架构: sm_89（RTX 40 系列，含 4050/4060）
echo  关键选项: GGML_CUDA_NO_VMM=ON
echo  nvcc:
"%CUDA_HOME%\bin\nvcc.exe" --version
echo  预计耗时: 30~60 分钟
echo ========================================
echo.

py -3 -m pip uninstall pywhispercpp -y >nul 2>&1
py -3 -m pip install --no-cache-dir --force-reinstall git+https://github.com/absadiki/pywhispercpp
if errorlevel 1 (
    echo 编译安装失败。
    pause
    exit /b 1
)

py -3 "%~dp0_verify_cuda_gpu.py"
if errorlevel 1 (
    echo GPU 验证失败。
    pause
    exit /b 1
)

echo.
echo 安装完成！请重新启动播放器或转写工具，并将「推理设备」设为 GPU 或自动。
pause
