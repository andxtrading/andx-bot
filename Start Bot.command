#!/bin/zsh
# ANDX Trading Bot — Mac launcher. Double-click to run.
# First launch sets up Python automatically (about a minute).
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ and run this again."
  read "?Press Enter to close..."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run — setting up (about a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if lsof -ti :8300 >/dev/null 2>&1; then
  open "http://127.0.0.1:8300"
  echo "The bot is already running — opened the dashboard."
  exit 0
fi

echo "Starting the ANDX Trading Bot..."
.venv/bin/python app.py &
SERVER_PID=$!
sleep 3
open "http://127.0.0.1:8300"
echo ""
echo "Dashboard: http://127.0.0.1:8300"
echo "Keep this window open while the bot runs. Close it (or Ctrl+C) to stop."
wait $SERVER_PID
