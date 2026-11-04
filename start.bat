@echo off
setlocal
title Product Card Assistant
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot open the project directory.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run setup.bat first.
    goto fail
)
".venv\Scripts\python.exe" -c "import app.main"
if errorlevel 1 (
    echo [ERROR] Check .env settings and run setup.bat to install dependencies.
    goto fail
)

echo Open http://127.0.0.1:8000 in your browser.
echo Configure the model API in .env, then click Check connection on the page.
echo Press Ctrl+C to stop the application.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo [ERROR] Application stopped with an error. Check the messages above.
popd
pause
exit /b %RESULT%

:fail
popd
pause
exit /b 1
