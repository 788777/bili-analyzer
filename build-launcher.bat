@echo off
REM ============================================================
REM  bili-analyzer launcher build script (PyInstaller)
REM  Output: dist\bili-launcher\bili-launcher.exe
REM  NOTE: keep this file ASCII-only, otherwise cmd may break
REM        the commands under non-UTF8 code pages.
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo  bili-analyzer launcher build
echo  Output directory: dist\bili-launcher\
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo         Please run the following commands first:
    echo             py -3.12 -m venv .venv
    echo             .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [1/2] Checking PyInstaller ...
".venv\Scripts\python.exe" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller not found, installing ...
    ".venv\Scripts\python.exe" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller. Check your network, or run:
        echo         .venv\Scripts\python.exe -m pip install pyinstaller
        pause
        exit /b 1
    )
)

echo [2/2] Building ... this takes about 1-3 minutes.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean launcher.spec
if errorlevel 1 (
    echo.
    echo [FAILED] Build did not finish. See the messages above.
    pause
    exit /b 1
)

echo.
echo [OK] Build finished!
echo      Executable: dist\bili-launcher\bili-launcher.exe
echo      Keep this folder inside the project directory so that
echo      launcher.config.json can be found automatically.
echo.
pause
