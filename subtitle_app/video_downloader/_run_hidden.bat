@echo off
chcp 65001 >nul
cd /d "%~dp0"

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

set NO_PROXY=*
set no_proxy=*
set "SUBTITLE_HIDE_CONSOLE=1"
%SUBTITLE_PYTHON% main.py
exit /b %ERRORLEVEL%
