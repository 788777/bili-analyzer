@echo off
REM ============================================================
REM  Start the bili-analyzer launcher GUI without building it.
REM  Uses the project virtual environment if available.
REM  NOTE: keep this file ASCII-only, otherwise cmd may break
REM        the commands under non-UTF8 code pages.
REM ============================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "launcher.py"
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" "launcher.py"
    exit /b 0
)

where pythonw.exe >nul 2>&1 && (
    start "" pythonw.exe "launcher.py"
    exit /b 0
)

where python.exe >nul 2>&1 && (
    start "" python.exe "launcher.py"
    exit /b 0
)

echo [ERROR] Python not found.
echo         Create the virtual environment first:
echo             py -3.12 -m venv .venv
echo             .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
