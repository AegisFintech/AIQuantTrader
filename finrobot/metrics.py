from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb

from finrobot import data_store, validators


WAREHOUSE_TABLES = ("status", "positions", "deals", "acks")
MIN_CLOCK_SKEW_ROWS = 1


@dataclass
class MetricsSnapshot:
    timestamp_local: int
    timestamp_iso: str
    heartbeat_age_seconds: float | None
    heartbeat_stale: bool
    daily_pnl: float | None
    daily_loss_limit_reached: bool | None
    open_managed_positions: int | None
    balance: float | None
    equity: float | None
    margin: float | None
    risk_lot_sizing_active: bool | None
    per_symbol: dict[str, dict[str, Any]]
    warehouse: dict[str, int]
    warehouse_freshness: dict[str, int]
    clock_skew_seconds: int | None
    pm2_mt5_restarts: int | None
    validator_issues: dict[str, Any]


def compute_snapshot(
    *,
    con: duckdb.DuckDBPyConnection,
    common_dir: Path | None,
    pm2_restarts: int | None,
    heartbeat_stale_seconds: int = 60,
    freshness_window_seconds: int = 300,
    clock_skew_window_seconds: int = 600,
) -> MetricsSnapshot:
    """Compute a metrics snapshot from an open warehouse and Common Files path."""
    timestamp_local = int(time.time())
    status_path = common_dir / "finrobot_status.json" if common_dir is not None else None
    heartbeat_age = _heartbeat_age_seconds(status_path, timestamp_local)
    status = _read_status(status_path)
    money_management = status.get("money_management") if isinstance(status, dict) else None
    if not isinstance(money_management, dict):
        money_management = {}

    warehouse = _warehouse_counts(con)
    return MetricsSnapshot(
        timestamp_local=timestamp_local,
        timestamp_iso=datetime.fromtimestamp(timestamp_local, timezone.utc).isoformat(),
        heartbeat_age_seconds=heartbeat_age,
        heartbeat_stale=heartbeat_age is not None
        and heartbeat_age > heartbeat_stale_seconds,
        daily_pnl=_float_or_none(money_management.get("today_closed_pnl")),
        daily_loss_limit_reached=_bool_or_none(money_management.get("loss_limit_reached")),
        open_managed_positions=_int_or_none(status.get("positions")),
        balance=_float_or_none(status.get("balance")),
        equity=_float_or_none(status.get("equity")),
        margin=_float_or_none(status.get("margin")),
        risk_lot_sizing_active=_bool_or_none(money_management.get("risk_lot_sizing")),
        per_symbol=_per_symbol(status.get("symbols")),
        warehouse=warehouse,
        warehouse_freshness=_warehouse_freshness(
            con, timestamp_local, freshness_window_seconds
        ),
        clock_skew_seconds=_clock_skew_seconds(
            con, timestamp_local, clock_skew_window_seconds
        ),
        pm2_mt5_restarts=pm2_restarts,
        validator_issues=_validator_issues(con),
    )


def get_pm2_restarts(process_name: str = "mt5-terminal") -> int | None:
    """Return the PM2 restart count for a process, or None if unavailable."""
    try:
        proc = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (
        ProcessLookupError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
    ):
        return None
    if proc.returncode != 0:
        return None
    try:
        apps = json.loads(proc.stdout or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(apps, list):
        return None
    for app in apps:
        if not isinstance(app, dict) or app.get("name") != process_name:
            continue
        try:
            return int((app.get("pm2_env") or {}).get("restart_time"))
        except (TypeError, ValueError):
            return None
    return None


def snapshot_to_dict(snap: MetricsSnapshot) -> dict[str, Any]:
    """Return a JSON-serializable dictionary for a metrics snapshot."""
    return _json_safe(asdict(snap))


def write_snapshot(snap: MetricsSnapshot, path: Path) -> None:
    """Write a metrics snapshot to a pretty-printed JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot_to_dict(snap), indent=2, sort_keys=True) + "\n")


def load_snapshot(path: Path) -> dict[str, Any] | None:
    """Load a previously written snapshot, returning None if unavailable."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _heartbeat_age_seconds(path: Path | None, timestamp_local: int) -> float | None:
    if path is None or not path.exists():
        return None
    try:
        return max(0.0, float(timestamp_local) - path.stat().st_mtime)
    except OSError:
        return None


def _read_status(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or not path.stat().st_size:
        return {}
    try:
        payload = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _warehouse_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    try:
        summary = data_store.query_summary(con)
    except duckdb.Error:
        return {table: 0 for table in WAREHOUSE_TABLES}
    return {table: int(summary.get(table) or 0) for table in WAREHOUSE_TABLES}


def _warehouse_freshness(
    con: duckdb.DuckDBPyConnection,
    timestamp_local: int,
    freshness_window_seconds: int,
) -> dict[str, int]:
    cutoff = timestamp_local - max(0, int(freshness_window_seconds))
    freshness: dict[str, int] = {}
    for table in WAREHOUSE_TABLES:
        try:
            row = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ts_local >= ?",
                [cutoff],
            ).fetchone()
        except duckdb.Error:
            freshness[table] = 0
            continue
        freshness[table] = int(row[0] if row else 0)
    return freshness


def _clock_skew_seconds(
    con: duckdb.DuckDBPyConnection,
    timestamp_local: int,
    clock_skew_window_seconds: int,
) -> int | None:
    cutoff = timestamp_local - max(0, int(clock_skew_window_seconds))
    try:
        row = con.execute(
            """
            SELECT COUNT(*), median(delta)
            FROM (
              SELECT ts_server - ts_local AS delta
              FROM status
              WHERE ts_local >= ?
              ORDER BY ts_server DESC
              LIMIT ?
            )
            """,
            [cutoff, max(0, int(clock_skew_window_seconds))],
        ).fetchone()
    except duckdb.Error:
        return None
    if row is None:
        return None
    count, median_skew = row
    if int(count or 0) < MIN_CLOCK_SKEW_ROWS or median_skew is None:
        return None
    return int(round(float(median_skew)))


def _validator_issues(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    try:
        issues = validators.validate_warehouse(con)
    except duckdb.Error:
        return {"errors": 0, "warnings": 0, "by_check": {}}

    errors = 0
    warnings = 0
    by_check: dict[str, int] = {}
    for issue in issues:
        if issue.severity == validators.Severity.ERROR:
            errors += 1
        elif issue.severity == validators.Severity.WARNING:
            warnings += 1
        by_check[issue.check] = by_check.get(issue.check, 0) + 1
    return {"errors": errors, "warnings": warnings, "by_check": by_check}


def _per_symbol(symbols: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(symbols, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in symbols:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        result[symbol] = {
            "last_signal": item.get("last_signal"),
            "auto_positions": _int_or_none(item.get("auto_positions")),
            "session_open": _bool_or_none(item.get("session_open")),
            "spread_points": _float_or_none(item.get("spread_points")),
        }
    return result


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    number = _int_or_none(value)
    if number is None:
        return None
    return bool(number)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value
