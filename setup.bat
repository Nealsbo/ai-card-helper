@echo off
setlocal
title Product Card Assistant - Setup
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot open the project directory.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" goto install

echo [1/3] Looking for Python 3.11 or newer...
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto create_venv
)
py -3.11 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.11"
    goto create_venv
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto create_venv
)
echo [ERROR] Python 3.11 or newer was not found. Install Python and run setup.bat again.
goto fail

:create_venv
echo Creating .venv...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto fail

:install
echo [2/3] Installing project dependencies...
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
    echo [ERROR] The existing .venv must use Python 3.11 or newer.
    goto fail
)
".venv\Scripts\python.exe" -m pip install -e ".[dev]" -c constraints.txt
if errorlevel 1 goto fail

echo [3/3] Preparing configuration...
if exist ".env" goto ready
copy /Y ".env.example" ".env" >nul
if errorlevel 1 goto fail

:ready
echo.
echo Setup complete. Your existing .env settings are preserved.
echo Configure your local or cloud model API in .env, then run start.bat.
popd
pause
exit /b 0

:fail
echo.
echo [ERROR] Setup failed. Check the messages above and run setup.bat again.
popd
pause
exit /b 1
