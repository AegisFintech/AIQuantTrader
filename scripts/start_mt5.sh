#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
RUNTIME_DIR="${AIQUANTTRADER_RUNTIME_DIR:-$ROOT/.runtime}"
MT5_DIR="${AIQUANTTRADER_MT5_DIR:-$RUNTIME_DIR/mt5}"
export WINEPREFIX="${AIQUANTTRADER_WINEPREFIX:-$RUNTIME_DIR/wineprefix}"
export WINEARCH=win64
export WINEDEBUG=${WINEDEBUG:--all}
WINE_CMD="${AIQUANTTRADER_WINE_CMD:-wine}"
ARCH="$(uname -m)"
if [ "${AIQUANTTRADER_ALLOW_EMULATED_MT5:-false}" = "true" ] && [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "amd64" ] && [ -z "${AIQUANTTRADER_WINE_CMD:-}" ]; then
  if command -v wine >/dev/null 2>&1 && wine --version 2>/dev/null | grep -qi 'hangover'; then
    WINE_CMD="wine"
  else
    WINE_CMD="$ROOT/scripts/wine_box64.sh"
  fi
fi
read -r -a WINE_CMD_ARR <<< "$WINE_CMD"
TERMINAL="$MT5_DIR/terminal/current/terminal64.exe"
if [ ! -f "$TERMINAL" ]; then
  echo "MT5 terminal not found; run ./install.sh first" >&2
  exit 1
fi
mkdir -p "$ROOT/logs"
mkdir -p "$MT5_DIR/terminal/current/Config"
cat > "$MT5_DIR/terminal/current/Config/aiquanttrader-login.ini" <<INI
[Common]
Login=${MT5_LOGIN:-}
Password=${MT5_PASSWORD:-}
Server=${MT5_SERVER:-}
ProxyEnable=0
NewsEnable=0
CertInstall=0
INI
chmod 600 "$MT5_DIR/terminal/current/Config/aiquanttrader-login.ini"
if [ "${AIQUANTTRADER_CONFIGURE_PROFILE_ON_START:-true}" = "true" ]; then
  PYTHON="$ROOT/.venv/bin/python"
  if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
  fi
  "$PYTHON" "$ROOT/scripts/mt5_configure_profile.py"
fi
cd "$MT5_DIR/terminal/current"
exec xvfb-run -a "${WINE_CMD_ARR[@]}" "$TERMINAL" /portable /config:Config\\aiquanttrader-login.ini
