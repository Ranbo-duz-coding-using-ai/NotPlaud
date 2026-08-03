@echo off
REM Double-click this in Explorer to open NotPlaud on Windows.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found. Setting one up...
    python -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install -r notplaud_app\requirements.txt || goto :error
)

REM pythonw runs without leaving a console window behind.
start "" ".venv\Scripts\pythonw.exe" "notplaud_app\desktop.py"
exit /b 0

:error
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on your PATH.
pause
exit /b 1
