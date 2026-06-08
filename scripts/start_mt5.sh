#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
RUNTIME_DIR="${FINROBOT_RUNTIME_DIR:-$ROOT/.runtime}"
MT5_DIR="${FINROBOT_MT5_DIR:-$RUNTIME_DIR/mt5}"
export WINEPREFIX="${FINROBOT_WINEPREFIX:-$RUNTIME_DIR/wineprefix}"
export WINEARCH=win64
export WINEDEBUG=${WINEDEBUG:--all}
TERMINAL="$MT5_DIR/terminal/current/terminal64.exe"
if [ ! -f "$TERMINAL" ]; then
  echo "MT5 terminal not found; run ./install.sh first" >&2
  exit 1
fi
mkdir -p "$ROOT/logs"
cd "$MT5_DIR/terminal/current"
exec xvfb-run -a wine "$TERMINAL" /portable /config:Config\\finrobot-login.ini
