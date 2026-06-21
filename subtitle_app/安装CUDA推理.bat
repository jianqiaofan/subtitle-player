@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "CUDA124=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
set "CUDA129=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9"

if exist "%CUDA124%\bin\nvcc.exe" (
    set "CUDA_HOME=%CUDA124%"
    echo 使用 CUDA 12.4
) else if exist "%CUDA129%\bin\nvcc.exe" (
    set "CUDA_HOME=%CUDA129%"
    echo 未检测到 CUDA 12.4，回退使用 CUDA 12.9
) else (
    echo 未找到 CUDA Toolkit，请先安装 CUDA 12.4 或 12.9。
    pause
    exit /b 1
)

call "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
    echo 未找到 Visual Studio 2022 构建环境。
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
echo  显卡: RTX 4060 Ti (sm_89)
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
echo 安装完成！请重新启动「启动转写工具.bat」。
pause
