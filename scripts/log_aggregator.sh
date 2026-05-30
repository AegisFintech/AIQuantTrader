#!/usr/bin/env bash
# FinRobot unified log aggregator.
# Tails the meaningful runtime logs into a single combined.log with source
# tags and timestamps for better traceability. Purely additive: it does NOT
# modify trading behavior or the source logs. Managed by PM2 (log-aggregator).
set -uo pipefail
ROOT="/home/openclaw/FinRobot"
LOGDIR="$ROOT/logs"
OUT="$LOGDIR/combined.log"
MAX_BYTES=$((50*1024*1024))   # rotate combined.log past ~50MB
mkdir -p "$LOGDIR"

# Curated, de-duplicated active sources. pm2 *.out mirrors of app logs are
# intentionally excluded to avoid double-logging (e.g. autonomous_review).
SOURCES=(
  "mt5_terminal.log"
  "autonomous_review.log"
  "pm2_autonomous_review.err.log"
  "pm2_dashboard.err.log"
  "pm2_mt5.err.log"
)

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
echo "$(ts) [aggregator] start pid=$$ sources=${SOURCES[*]}" >> "$OUT"

pids=()
for f in "${SOURCES[@]}"; do
  src="${f%.log}"
  touch "$LOGDIR/$f"
  ( tail -F -n0 "$LOGDIR/$f" 2>/dev/null | while IFS= read -r line; do
      printf '%s [%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$src" "$line"
    done ) >> "$OUT" &
  pids+=($!)
done

# Lightweight size-based rotation watchdog.
( while true; do
    sleep 300
    if [ -f "$OUT" ]; then
      sz=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
      if [ "$sz" -gt "$MAX_BYTES" ]; then
        tail -n 5000 "$OUT" > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
        echo "$(ts) [aggregator] rotated combined.log (was ${sz} bytes)" >> "$OUT"
      fi
    fi
  done ) &
pids+=($!)

trap 'kill "${pids[@]}" 2>/dev/null' EXIT INT TERM
wait
