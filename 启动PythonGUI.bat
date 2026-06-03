@echo off
chcp 65001 >nul
cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.10+。
  pause
  exit /b 1
)

python -m pip show playwright >nul 2>nul
if errorlevel 1 (
  echo 正在安装 Python Playwright 依赖...
  python -m pip install -r requirements-python.txt
  if errorlevel 1 (
    echo 依赖安装失败。
    pause
    exit /b 1
  )
)

python -m playwright install chromium
if errorlevel 1 (
  echo Playwright Chromium 安装失败。
  pause
  exit /b 1
)
python dy_grab_gui.py
pause
