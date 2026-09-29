#!/bin/bash
# ANDX Trading Bot — one-command installer (Mac/Linux).
# Downloading via Terminal avoids the macOS "unidentified developer" warning,
# because files fetched with curl never get the browser "quarantine" flag.
set -e

DIR="$HOME/andx-bot"
ZIP_URL="https://github.com/andxtrading/andx-bot/archive/refs/heads/main.zip"

if ! command -v python3 >/dev/null 2>&1; then
  echo ""
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  echo "then run this command again."
  exit 1
fi

# Apple/Xcode ship an old Python (3.9) that cannot run this app (it needs 3.10+).
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo ""
  echo "Your Python is too old (this bot needs Python 3.10 or newer)."
  echo "Install the latest from https://www.python.org/downloads/ then run this command again."
  exit 1
fi

if [ ! -d "$DIR" ]; then
  echo "Downloading the ANDX Trading Bot..."
  curl -fsSL "$ZIP_URL" -o /tmp/andx-bot.zip
  rm -rf /tmp/andx-bot-extract && mkdir -p /tmp/andx-bot-extract
  unzip -q /tmp/andx-bot.zip -d /tmp/andx-bot-extract
  mv /tmp/andx-bot-extract/andx-bot-main "$DIR"
  echo "Setting up (about a minute)..."
  cd "$DIR"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
else
  echo "Bot already installed — starting it (your edits are kept)."
  cd "$DIR"
  [ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
fi

if lsof -ti :8300 >/dev/null 2>&1; then
  echo "The bot is already running."
else
  echo "Starting the bot..."
  nohup .venv/bin/python app.py >/tmp/andx-bot.log 2>&1 &
  sleep 3
fi

open "http://127.0.0.1:8300" 2>/dev/null || echo "Open http://127.0.0.1:8300 in your browser."
echo ""
echo "Done! The dashboard should be open in your browser."
echo "Your bot files are in: $DIR"
echo "It runs in the background — press Stop on the dashboard to stop it."
