@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"
set "LAUNCHER=%~dp0launcher.py"
set "REQ=%~dp0requirements.txt"

if not exist "%LAUNCHER%" (
    echo [错误] 找不到 launcher.py
    echo 请确认本脚本放在项目根目录（与 launcher.py 同级）。
    pause
    exit /b 1
)

REM ---- 找系统 Python（优先 py -3，再 python）----
set "SYS_PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 set "SYS_PY=py -3"
)
if not defined SYS_PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%I in ('where python') do (
            if not defined SYS_PY set "SYS_PY=%%I"
        )
    )
)
if not defined SYS_PY (
    echo [错误] 未找到 Python 3.10+。
    echo 请先安装 Python 3.10 或更高版本，并勾选 "Add python.exe to PATH"。
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ---- 无 .venv 则创建并装依赖 ----
if not exist "%VENV_PY%" (
    echo [准备] 首次运行：正在创建虚拟环境 .venv ...
    %SYS_PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。请确认已安装 Python 3.10+。
        pause
        exit /b 1
    )
    if not exist "%VENV_PY%" (
        echo [错误] 虚拟环境创建后仍找不到:
        echo   %VENV_PY%
        pause
        exit /b 1
    )
    echo [准备] 正在安装依赖（优先清华源）...
    "%VENV_PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    "%VENV_PY%" -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [警告] 清华源安装失败，改用默认 PyPI...
        "%VENV_PY%" -m pip install -r "%REQ%"
        if errorlevel 1 (
            echo [错误] 依赖安装失败。请检查网络后重试，或手动执行:
            echo   "%VENV_PY%" -m pip install -r requirements.txt
            pause
            exit /b 1
        )
    )
    echo [准备] 依赖安装完成。
)

REM ---- 若 venv 存在但缺包，补装一次 ----
"%VENV_PY%" -c "import cv2,numpy,PIL" >nul 2>nul
if errorlevel 1 (
    echo [准备] 检测到依赖不完整，正在补装...
    "%VENV_PY%" -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 "%VENV_PY%" -m pip install -r "%REQ%"
    "%VENV_PY%" -c "import cv2,numpy,PIL" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 仍无法 import opencv/numpy/Pillow。
        pause
        exit /b 1
    )
)

REM ---- 启动 GUI：优先无控制台 pythonw ----
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "%LAUNCHER%"
    exit /b 0
)
if exist "%VENV_PY%" (
    start "" "%VENV_PY%" "%LAUNCHER%"
    exit /b 0
)

echo [错误] 找不到 .venv 里的 Python。
pause
exit /b 1
