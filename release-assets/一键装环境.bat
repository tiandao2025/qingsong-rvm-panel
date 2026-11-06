@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo   青松口播抠像面板 · 一键装环境
echo ==========================================
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)
if not defined PY (
    if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
)
if not defined PY (
    echo [错误] 没有检测到 Python。
    echo.
    echo 请先安装 Python 3.11，安装时务必勾选 "Add Python to PATH"：
    echo   https://www.python.org/downloads/
    echo.
    echo 装好后再双击本文件。
    pause
    exit /b 1
)

echo 使用解释器: %PY%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 创建虚拟环境 .venv ...
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

echo [2/4] 升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo.
echo [3/4] 安装 PyTorch CUDA 12.8 版（约 2~3 GB，请耐心等待，不要关窗口）...
".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
    echo [错误] PyTorch 安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo.
echo [4/4] 安装 av / pillow / numpy ...
".venv\Scripts\python.exe" -m pip install av pillow numpy

echo.
echo ==========================================
echo   环境安装完成，现在可以双击
echo   qingsong-rvm-panel.exe 开始使用。
echo ==========================================
pause
endlocal
