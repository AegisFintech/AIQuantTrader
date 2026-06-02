#!/usr/bin/env bash
set -euo pipefail
if [ -f /home/openclaw/FinRobot/.env ]; then set -a; source /home/openclaw/FinRobot/.env; set +a; fi
ROOT="/home/openclaw/FinRobot"
export WINEPREFIX="/home/openclaw/.wine-mt5"
export WINEARCH=win64
export WINEDEBUG=${WINEDEBUG:--all}
TERMINAL="/home/openclaw/mt5/terminal/current/terminal64.exe"
if [ ! -f "$TERMINAL" ]; then
  echo "MT5 terminal not found; run scripts/setup_mt5_headless.sh first" >&2
  exit 1
fi
mkdir -p "$ROOT/logs"
cd "/home/openclaw/mt5/terminal/current"
exec xvfb-run -a wine "$TERMINAL" /portable /config:Config\\finrobot-login.ini
