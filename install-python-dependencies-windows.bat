@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo EDOPro HD Pics Downloader dependency installer (Windows)
echo Required: Python 3.12, tkinter, Python Pillow
echo tkinter is included with the normal Windows Python installer.
echo.

set "PYTHON_CMD="

py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3.12"

if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    python3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python3.12"
)

if not defined PYTHON_CMD (
    echo Python 3.12 was not found.
    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget was not found. Opening the Python downloads page.
        start "" "https://www.python.org/downloads/"
        echo Install Python 3.12, then run this file again.
        pause
        exit /b 1
    )

    echo Installing Python 3.12 with winget...
    set "PYLAUNCHER_ALLOW_INSTALL=1"
    winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Python 3.12 install failed.
        pause
        exit /b 1
    )

    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)

if not defined PYTHON_CMD (
    echo Python 3.12 is still not available.
    echo Close and reopen this window, then run this file again.
    echo If it still fails, reinstall Python 3.12 and enable Add python.exe to PATH.
    pause
    exit /b 1
)

echo Using Python command: %PYTHON_CMD%

echo Upgrading pip...
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 (
    echo pip upgrade failed.
    pause
    exit /b 1
)

echo Installing Python Pillow...
%PYTHON_CMD% -m pip install --upgrade Pillow
if errorlevel 1 (
    echo Python Pillow install failed.
    pause
    exit /b 1
)

echo.
echo Done. You can run the downloader with:
echo %PYTHON_CMD% EDOPro-Standard-Rush-HD-Pics-Downloader.py
pause
