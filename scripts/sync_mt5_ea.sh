#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

cd "$TERMINAL_DIR"
xvfb-run -a wine "$METAEDITOR" /compile:"MQL5\\Experts\\FinRobot\\FinRobotBridgeEA.mq5" /log:compile.log || true
echo "EA files synced. Compile log: $TERMINAL_DIR/compile.log"
