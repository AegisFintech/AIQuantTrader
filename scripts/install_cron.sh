#!/usr/bin/env bash
# Install the FinRobot cron policy.
#
# Copies config/finrobot.cron into /etc/cron.d/finrobot and validates the file
# with the local crontab dry-run syntax checker.
#
# Idempotent: safe to re-run; it overwrites the destination.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/config/finrobot.cron"
DEST="/etc/cron.d/finrobot"

if [ ! -f "$SRC" ]; then
    echo "Source cron file not found: $SRC" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-run as root (this script writes to $DEST):" >&2
    echo "  sudo $0" >&2
    exit 1
fi

if ! command -v crontab >/dev/null 2>&1; then
    echo "crontab command not found; install cron before running this installer" >&2
    exit 1
fi

cron_help="$(crontab -h 2>&1 || true)"

if grep -q -- "-T" <<<"$cron_help"; then
    syntax_flag="-T"
elif grep -q -- "-n" <<<"$cron_help"; then
    syntax_flag="-n"
else
    echo "crontab does not expose a supported syntax-check flag (-T or -n)" >&2
    exit 1
fi

crontab "$syntax_flag" "$SRC" >/dev/null
install -m 0644 "$SRC" "$DEST"
crontab "$syntax_flag" "$DEST" >/dev/null

echo "[OK] installed $DEST"
echo "Cron syntax check: OK (crontab $syntax_flag $DEST)"
echo "Uninstall with:"
echo "  sudo rm /etc/cron.d/finrobot"
