@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    echo Python environment not found: "%PYTHONW%"
    echo Configure the project environment in VS Code, then install requirements.txt.
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%~dp0launcher.py"