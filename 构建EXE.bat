@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python，请先安装 Python 3.10 或更高版本。
  pause
  exit /b 1
)

echo [1/3] 安装或更新打包依赖...
python -m pip install -r requirements-build.txt
if errorlevel 1 (
  echo 依赖安装失败。
  pause
  exit /b 1
)

echo [2/3] 运行核心测试...
python -m unittest discover -s tests -v
if errorlevel 1 (
  echo 核心测试失败，已停止构建。
  pause
  exit /b 1
)

echo [3/3] 构建 Windows 程序...
python -m PyInstaller --noconfirm --clean dy_live.spec
if errorlevel 1 (
  echo EXE 构建失败，请查看上方错误信息。
  pause
  exit /b 1
)

copy /Y "用户使用说明.md" "dist\DYLiveAssistant\用户使用说明.md" >nul

echo.
echo 构建完成：dist\DYLiveAssistant\DYLiveAssistant.exe
echo 请分发整个 dist\DYLiveAssistant 文件夹，不能只复制 EXE。
pause
