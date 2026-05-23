#!/usr/bin/env bash
set -euo pipefail
if [ -f /home/openclaw/FinRobot/.env ]; then set -a; source /home/openclaw/FinRobot/.env; set +a; fi

ROOT="/home/openclaw/FinRobot"
MT5_HOME="/home/openclaw/mt5"
WINEPREFIX_DIR="/home/openclaw/.wine-mt5"
INSTALLER="$MT5_HOME/mt5setup.exe"
TERMINAL_DIR="$MT5_HOME/terminal"
LOG_DIR="$ROOT/logs"
mkdir -p "$MT5_HOME" "$TERMINAL_DIR" "$LOG_DIR"

export WINEPREFIX="$WINEPREFIX_DIR"
export WINEARCH=win64

if [ ! -f "$INSTALLER" ]; then
  curl -L --retry 3 --connect-timeout 20 -o "$INSTALLER" "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
fi

xvfb-run -a bash -lc 'wineboot -u >/dev/null 2>&1 || true'

# Official installer supports /auto and /path for unattended install.
xvfb-run -a wine "$INSTALLER" /auto "/path:C:\\FinRobotMT5" > "$LOG_DIR/mt5_install.log" 2>&1 || true

FOUND=""
for candidate in \
  "$WINEPREFIX_DIR/drive_c/FinRobotMT5/terminal64.exe" \
  "$WINEPREFIX_DIR/drive_c/Program Files/MetaTrader 5/terminal64.exe" \
  "$WINEPREFIX_DIR/drive_c/Program Files (x86)/MetaTrader 5/terminal64.exe"; do
  if [ -f "$candidate" ]; then FOUND="$candidate"; break; fi
done
if [ -z "$FOUND" ]; then
  FOUND=$(find "$WINEPREFIX_DIR/drive_c" -iname terminal64.exe | head -1 || true)
fi
if [ -z "$FOUND" ]; then
  echo "terminal64.exe not found. See $LOG_DIR/mt5_install.log" >&2
  exit 1
fi
ln -sfn "$(dirname "$FOUND")" "$TERMINAL_DIR/current"

mkdir -p "$TERMINAL_DIR/current/MQL5/Experts/FinRobot" "$TERMINAL_DIR/current/Config" "$TERMINAL_DIR/common-files"
cp "$ROOT/broker/mt5/FinRobotBridgeEA.mq5" "$TERMINAL_DIR/current/MQL5/Experts/FinRobot/FinRobotBridgeEA.mq5"

cat > "$TERMINAL_DIR/current/Config/finrobot-login.ini" <<INI
[Common]
Login=${MT5_LOGIN:-52606973}
Password=${MT5_PASSWORD:-}
Server=${MT5_SERVER:-ICMarketsSC-Demo}
ProxyEnable=0
NewsEnable=0
CertInstall=0
INI
chmod 600 "$TERMINAL_DIR/current/Config/finrobot-login.ini"

echo "MT5 terminal: $FOUND"
echo "Bridge EA copied to: $TERMINAL_DIR/current/MQL5/Experts/FinRobot/FinRobotBridgeEA.mq5"
