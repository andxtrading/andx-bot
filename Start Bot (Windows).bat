@echo off
REM ANDX Trading Bot — Windows launcher. Double-click to run.
cd /d "%~dp0"

where py >nul 2>nul || where python >nul 2>nul || (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add Python to PATH" during install, then run this again.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run - setting up, about a minute...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)

echo Starting the ANDX Trading Bot...
start "ANDX Trading Bot" .venv\Scripts\python app.py
timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:8300
echo.
echo Dashboard: http://127.0.0.1:8300
echo The bot runs in the other window - close it to stop.
pause
