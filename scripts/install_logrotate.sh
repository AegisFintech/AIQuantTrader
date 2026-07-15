#!/usr/bin/env bash
# Install the AIQuantTrader logrotate policy.
#
# Copies config/logrotate-aiquanttrader into /etc/logrotate.d/aiquanttrader, validates
# the file with `logrotate --debug`, and prints a one-line cron hint.
#
# Idempotent: safe to re-run; it overwrites the destination.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/config/logrotate-aiquanttrader"
DEST="/etc/logrotate.d/aiquanttrader"

if [ ! -f "$SRC" ]; then
    echo "Source policy not found: $SRC" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-run as root (this script writes to $DEST):" >&2
    echo "  sudo $0" >&2
    exit 1
fi

install -m 0644 "$SRC" "$DEST"
echo "Installed: $DEST"

if command -v logrotate >/dev/null 2>&1; then
    echo
    echo "Dry-run validation (no changes made):"
    logrotate --debug "$DEST" 2>&1 | head -20 || true
fi

echo
echo "Cron hint (run logrotate daily at 00:05 if not already in /etc/cron.daily):"
echo '  5 0 * * * /usr/sbin/logrotate /etc/logrotate.conf'
