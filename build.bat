@echo off
REM ============================================================
REM  bili-analyzer build script (PyInstaller)
REM  Output: dist\bili-analyzer\bili-analyzer.exe
REM  NOTE: keep this file ASCII-only, otherwise cmd may break
REM        the commands under non-UTF8 code pages.
REM ============================================================
setlocal
cd /d "%~dp0"

echo ============================================================
echo  bili-analyzer build
echo  Output directory: dist\bili-analyzer\
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

echo [1/3] Checking PyInstaller ...
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

echo [2/3] Building ... this takes about 3-10 minutes on the first run.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 (
    echo.
    echo [FAILED] Build did not finish. See the messages above;
    echo          troubleshooting steps are in README.md.
    pause
    exit /b 1
)

echo.
echo [3/3] Build finished!
echo       Executable: dist\bili-analyzer\bili-analyzer.exe
echo       Copy the whole dist\bili-analyzer folder when distributing.
echo.
pause
