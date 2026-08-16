@echo off
chcp 65001 >nul
cd /d "%~dp0"

call :resolve_python
if errorlevel 1 (
  pause
  exit /b 1
)

set NO_PROXY=*
set no_proxy=*
echo Installing / checking dependencies...
%SUBTITLE_PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo pip 安装失败时，可先关闭系统代理后再试。
  pause
  exit /b 1
)

REM Close the visible console; a hidden cmd keeps running until the GUI exits.
if exist "%~dp0..\_hide_console.vbs" (
  wscript //nologo "%~dp0..\_hide_console.vbs" "%~dp0_run_hidden.bat"
  if not errorlevel 1 exit /b 0
)

:start_app
call :resolve_python
if errorlevel 1 exit /b 1
set NO_PROXY=*
set no_proxy=*
%SUBTITLE_PYTHON% main.py
exit /b %ERRORLEVEL%

:resolve_python
set "SUBTITLE_PYTHON="
if exist "%~dp0..\_check_python.bat" (
  call "%~dp0..\_check_python.bat"
  if errorlevel 1 set "SUBTITLE_PYTHON="
)
if not defined SUBTITLE_PYTHON (
  for %%V in (3.10 3.11 3.12 3.13 3.14 3) do (
    if not defined SUBTITLE_PYTHON (
      py -%%V -c "import customtkinter, yt_dlp" >nul 2>&1
      if not errorlevel 1 set "SUBTITLE_PYTHON=py -%%V"
    )
  )
)
if not defined SUBTITLE_PYTHON set "SUBTITLE_PYTHON=py -3"
exit /b 0
