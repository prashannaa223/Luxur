@echo off
rem LUXUR launcher for Windows. Double-click to run.
rem First run: creates a Python environment and installs everything (a few minutes).
setlocal
cd /d "%~dp0L.U.X.U.R.O.S - Copy" || goto :fail

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3.10 or newer is required: https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" during install, then run start.bat again.
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Setting up LUXUR for the first time. This takes a few minutes...
    %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"

fc /b "Backend\Requirement.txt" ".venv\installed.txt" >nul 2>nul
if errorlevel 1 (
    echo Installing requirements...
    "%VPY%" -m pip install --upgrade pip --quiet
    "%VPY%" -m pip install -r "Backend\Requirement.txt" || goto :fail
    copy /y "Backend\Requirement.txt" ".venv\installed.txt" >nul
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Put your API keys into the .env file that just opened, save it, then run start.bat again.
    start "" notepad ".env"
    pause
    exit /b 0
)

"%VPY%" main.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo LUXUR could not start. See the messages above.
pause
exit /b 1
