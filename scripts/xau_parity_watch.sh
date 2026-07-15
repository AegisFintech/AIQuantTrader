#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

DATA_SOURCE="database"
FROM_DATE="2026-06-11"
TO_DATE="$(date -u +%F)"
SYMBOL="XAUUSD"
REPORT_DIR="$ROOT/state/research/reports"
REGISTRY="$ROOT/data/aiquanttrader.duckdb"
VERBOSE="false"

usage() {
  cat <<'USAGE'
Usage: scripts/xau_parity_watch.sh [options]

Options:
  --data-source {mt5-export,database}  Refresh source before the retest (default: database)
  --from-date TEXT                     Start date for report counts (default: 2026-06-11)
  --to-date TEXT                       End date for report counts (default: today UTC)
  --symbol TEXT                        Symbol to count in reports (default: XAUUSD)
  --report-dir PATH                    Report output directory (default: state/research/reports)
  --registry TEXT                      DuckDB warehouse path (default: data/aiquanttrader.duckdb)
  --verbose                            Print command progress
  -h, --help                           Show this help
USAGE
}

die() {
  echo "[ERR] $*" >&2
  exit 1
}

resolve_path() {
  local value="$1"
  if [[ "$value" = /* ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$ROOT/$value"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-source)
      [[ $# -ge 2 ]] || die "--data-source requires a value"
      DATA_SOURCE="$2"
      shift 2
      ;;
    --from-date)
      [[ $# -ge 2 ]] || die "--from-date requires a value"
      FROM_DATE="$2"
      shift 2
      ;;
    --to-date)
      [[ $# -ge 2 ]] || die "--to-date requires a value"
      TO_DATE="$2"
      shift 2
      ;;
    --symbol)
      [[ $# -ge 2 ]] || die "--symbol requires a value"
      SYMBOL="$2"
      shift 2
      ;;
    --report-dir)
      [[ $# -ge 2 ]] || die "--report-dir requires a value"
      REPORT_DIR="$(resolve_path "$2")"
      shift 2
      ;;
    --registry)
      [[ $# -ge 2 ]] || die "--registry requires a value"
      REGISTRY="$(resolve_path "$2")"
      shift 2
      ;;
    --verbose)
      VERBOSE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$DATA_SOURCE" in
  database|mt5-export)
    ;;
  *)
    die "--data-source must be one of: mt5-export, database"
    ;;
esac

REPORT_DIR="$(resolve_path "$REPORT_DIR")"
REGISTRY="$(resolve_path "$REGISTRY")"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_PATH="$REPORT_DIR/xau_parity_${TIMESTAMP}.log"
JSON_PATH="$REPORT_DIR/xau_parity_${TIMESTAMP}.json"
PYTEST_TARGET="${AIQUANTTRADER_XAU_PARITY_PYTEST_TARGET:-tests/test_xau_parity_live.py}"

mkdir -p "$REPORT_DIR"

if [[ "$VERBOSE" == "true" ]]; then
  echo "[INFO] source=$DATA_SOURCE registry=$REGISTRY symbol=$SYMBOL from=$FROM_DATE to=$TO_DATE"
fi

if [[ "$DATA_SOURCE" == "mt5-export" ]]; then
  if [[ "$VERBOSE" == "true" ]]; then
    echo "[INFO] harvesting MT5 export into warehouse"
  fi
  "$PYTHON_BIN" "$ROOT/scripts/harvest_mt5_export.py" \
    --warehouse "$REGISTRY" \
    --symbols "$SYMBOL"
fi

[[ -f "$REGISTRY" ]] || die "DuckDB warehouse not found: $REGISTRY"

export AIQUANTTRADER_WAREHOUSE="$REGISTRY"

if [[ "$VERBOSE" == "true" ]]; then
  echo "[INFO] collecting $PYTEST_TARGET"
fi
"$PYTHON_BIN" -m pytest "$PYTEST_TARGET" -v --co -q >/dev/null

if [[ "$VERBOSE" == "true" ]]; then
  echo "[INFO] running $PYTEST_TARGET"
fi
set +e
"$PYTHON_BIN" -m pytest "$PYTEST_TARGET" -v --no-header -s 2>&1 | tee "$LOG_PATH"
PYTEST_STATUS="${PIPESTATUS[0]}"
set -e

"$PYTHON_BIN" - "$ROOT" "$REGISTRY" "$FROM_DATE" "$TO_DATE" "$SYMBOL" "$LOG_PATH" "$JSON_PATH" "$PYTEST_STATUS" <<'PY'
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

root = Path(sys.argv[1])
registry = Path(sys.argv[2])
from_date = sys.argv[3]
to_date = sys.argv[4]
symbol = sys.argv[5].upper()
log_path = Path(sys.argv[6])
json_path = Path(sys.argv[7])
pytest_status = int(sys.argv[8])

sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "scripts"))

try:
    import duckdb
except Exception as exc:  # pragma: no cover - exercised by operator environment.
    print(f"[ERR] duckdb package is required: {exc}", file=sys.stderr)
    raise SystemExit(1)

from aiquanttrader.backtest.parity_replay import FILLED_ACTIONS, load_acked_decisions
from scripts.runtime_paths import common_dir


def utc_epoch_start(value: str) -> int:
    return int(datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc).timestamp())


def utc_epoch_end(value: str) -> int:
    return int(datetime.combine(date.fromisoformat(value), time.max, tzinfo=timezone.utc).timestamp())


def load_bars() -> list[dict]:
    con = duckdb.connect(str(registry), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT ts_server, open, high, low, close, COALESCE(volume, 0.0)
            FROM prices
            WHERE upper(symbol) = ?
              AND ts_server >= ?
              AND ts_server <= ?
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
            ORDER BY ts_server ASC
            """,
            [symbol, utc_epoch_start(from_date), utc_epoch_end(to_date)],
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


def parse_live_status(text: str) -> str:
    if re.search(r"\bXPASS\b", text):
        return "XPASS"
    if re.search(r"\bXFAIL\b|\bxfailed\b", text, flags=re.IGNORECASE):
        return "XFAIL"
    return "ERROR"


def parse_match_rate(text: str) -> float | None:
    patterns = (
        r"live XAU parity: .*?\((?P<pct>\d+(?:\.\d+)?)%\)",
        r"match_rate[=:]\s*(?P<rate>0(?:\.\d+)?|1(?:\.0+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        if match.groupdict().get("pct") is not None:
            return float(match.group("pct")) / 100.0
        return float(match.group("rate"))
    return None


try:
    bars = load_bars()
except Exception as exc:
    print(f"[ERR] failed to query {registry}: {exc}", file=sys.stderr)
    raise SystemExit(1)

directory = common_dir()
acks_path = directory / "aiquanttrader_acks.csv" if directory is not None else root / "__missing_aiquanttrader_acks.csv"
decisions = load_acked_decisions(
    acks_path,
    from_date=from_date,
    to_date=to_date,
    symbol=symbol,
    bars=bars,
    bar_match_window=2,
    timezone_name="UTC",
)
filled = [decision for decision in decisions if decision.get("action") in FILLED_ACTIONS]
overlap = [decision for decision in filled if decision.get("bar_idx") is not None]

pytest_output = log_path.read_text(errors="replace") if log_path.exists() else ""
live_status = parse_live_status(pytest_output)
if pytest_status != 0 and live_status in {"XPASS", "XFAIL"}:
    live_status = "ERROR"

payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "from_date": from_date,
    "to_date": to_date,
    "symbol": symbol,
    "n_bars_in_window": len(bars),
    "n_acks_in_window": len(filled),
    "overlap_count": len(overlap),
    "live_test_status": live_status,
    "match_rate": parse_match_rate(pytest_output),
}
json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

match_rate = payload["match_rate"]
match_text = "N/A" if match_rate is None else f"{match_rate:.2%}"
print(f"XAU parity retest: {live_status} | overlap={len(overlap)} | match_rate={match_text}")
raise SystemExit(0 if live_status in {"XPASS", "XFAIL"} else 1)
PY
