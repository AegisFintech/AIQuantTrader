#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
RUNTIME_DIR="${FINROBOT_RUNTIME_DIR:-$ROOT/.runtime}"
WINEPREFIX_DIR="${FINROBOT_WINEPREFIX:-$RUNTIME_DIR/wineprefix}"
MT5_DIR="${FINROBOT_MT5_DIR:-$RUNTIME_DIR/mt5}"
TERMINAL_DIR="$MT5_DIR/terminal/current"
EXPERT_DIR="$TERMINAL_DIR/MQL5/Experts/FinRobot"
COMPILE=1

if [ "${1:-}" = "--no-compile" ]; then
  COMPILE=0
fi

if [ ! -d "$TERMINAL_DIR" ]; then
  echo "MT5 terminal directory not found: $TERMINAL_DIR" >&2
  echo "Run ./install.sh first." >&2
  exit 1
fi

mkdir -p "$EXPERT_DIR"
cp "$ROOT/broker/mt5/FinRobotBridgeEA.mq5" "$EXPERT_DIR/FinRobotBridgeEA.mq5"
cp "$ROOT/broker/mt5/BridgeIO.mqh" "$EXPERT_DIR/BridgeIO.mqh"
cp "$ROOT/broker/mt5/RiskManagement.mqh" "$EXPERT_DIR/RiskManagement.mqh"
cp "$ROOT/broker/mt5/SmartMoney.mqh" "$EXPERT_DIR/SmartMoney.mqh"

# Refresh the M1 history exporter script.
EXPORT_SRC="$ROOT/broker/mt5/scripts/ExportM1Bars.mq5"
if [ -f "$EXPORT_SRC" ]; then
    cp "$EXPORT_SRC" "$TERMINAL_DIR/MQL5/Experts/ExportM1Bars.mq5"
fi

# Refresh the release manifest so the EA can read version + git_sha on init.
if MANIFEST_SRC="$ROOT/state/mt5/EA_MANIFEST.txt"; [ -f "$MANIFEST_SRC" ]; then
    cp "$MANIFEST_SRC" "$EXPERT_DIR/EA_MANIFEST.txt"
fi

if [ "$COMPILE" -eq 0 ]; then
  echo "EA files synced to $EXPERT_DIR"
  exit 0
fi

METAEDITOR="$TERMINAL_DIR/MetaEditor64.exe"
if [ ! -f "$METAEDITOR" ]; then
  echo "MetaEditor64.exe not found; synced files without compiling." >&2
  exit 0
fi

export WINEPREFIX="$WINEPREFIX_DIR"
export WINEARCH=win64
export WINEDEBUG="${WINEDEBUG:--all}"
WINE_CMD="${FINROBOT_WINE_CMD:-wine}"
ARCH="$(uname -m)"
if [ "${FINROBOT_ALLOW_EMULATED_MT5:-false}" = "true" ] && [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "amd64" ] && [ -z "${FINROBOT_WINE_CMD:-}" ]; then
  if command -v wine >/dev/null 2>&1 && wine --version 2>/dev/null | grep -qi 'hangover'; then
    WINE_CMD="wine"
  else
    WINE_CMD="$ROOT/scripts/wine_box64.sh"
  fi
fi
read -r -a WINE_CMD_ARR <<< "$WINE_CMD"

cd "$TERMINAL_DIR"
xvfb-run -a "${WINE_CMD_ARR[@]}" "$METAEDITOR" /compile:"MQL5\\Experts\\FinRobot\\FinRobotBridgeEA.mq5" /log:compile.log || true
echo "EA files synced. Compile log: $TERMINAL_DIR/compile.log"
