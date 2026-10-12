@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if !errorlevel!==0 (
  py -3 app.py
) else (
  where python >nul 2>nul
  if !errorlevel!==0 (
    python app.py
  ) else (
    echo 未找到Python。请先安装Python 3.10或更高版本，并勾选Add Python to PATH。
    pause
    exit /b 1
  )
)
if errorlevel 1 pause
