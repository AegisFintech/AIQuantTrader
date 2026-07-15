#!/usr/bin/env bash
# Install the AIQuantTrader MT5 M1 history exporter script.
#
# Copies broker/mt5/scripts/ExportM1Bars.mq5 into the terminal Experts
# directory so MetaEditor/MT5 can compile and run it manually.
#
# Idempotent: safe to re-run; it overwrites the destination.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

RUNTIME_DIR="${AIQUANTTRADER_RUNTIME_DIR:-$ROOT/.runtime}"
MT5_DIR="${AIQUANTTRADER_MT5_DIR:-$RUNTIME_DIR/mt5}"
TERMINAL_DIR="$MT5_DIR/terminal/current"
SRC="$ROOT/broker/mt5/scripts/ExportM1Bars.mq5"
DEST="$TERMINAL_DIR/MQL5/Experts/ExportM1Bars.mq5"

if [ ! -f "$SRC" ]; then
    echo "Source exporter not found: $SRC" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-run as root (this script writes to $TERMINAL_DIR/MQL5/Experts):" >&2
    echo "  sudo $0" >&2
    exit 1
fi

if [ ! -d "$TERMINAL_DIR" ]; then
    echo "MT5 terminal directory not found: $TERMINAL_DIR" >&2
    echo "Run ./install.sh first." >&2
    exit 1
fi

mkdir -p "$(dirname "$DEST")"
install -m 0644 "$SRC" "$DEST"

echo "[OK] installed: $DEST"
echo "In MT5, open a chart, drag the script from the Navigator onto it, set Symbol + BarCount inputs, click OK."
