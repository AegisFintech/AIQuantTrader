from __future__ import annotations

import csv
import json
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import duckdb

from finrobot.release_manifest import load_release_manifest


TSV_COLUMNS = ("time", "open", "high", "low", "close", "volume")
TSV_BIDASK_COLUMNS = ("time", "open", "high", "low", "close", "volume", "bid", "ask")
DEFAULT_SYNTHETIC_START_TS = 1704067200  # 2024-01-01 00:00:00 local time.


def init_prices_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the prices table if it does not exist. Idempotent."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
          symbol TEXT NOT NULL,
          ts_server INTEGER NOT NULL,
          ts_local  INTEGER NOT NULL,
          open  DOUBLE,
          high  DOUBLE,
          low   DOUBLE,
          close DOUBLE,
          volume DOUBLE,
          spread_price DOUBLE,
          spread_points DOUBLE,
          source TEXT NOT NULL,
          ea_version TEXT,
          git_sha TEXT,
          PRIMARY KEY (symbol, ts_server, source)
        )
        """
    )


def ingest_bars(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    bars: list[dict],
    *,
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert OHLCV bar rows, ignoring duplicate symbol/timestamp/source rows."""
    init_prices_schema(con)
    clean_symbol = _as_text(symbol)
    if not clean_symbol:
        raise ValueError("symbol is required")
    if not bars:
        return 0
    ea_version, git_sha = _release_defaults(ea_version, git_sha)
    rows = []
    for bar in bars:
        ts_server = _row_ts(bar)
        if ts_server is None:
            continue
        bid = _as_float(bar.get("bid"))
        ask = _as_float(bar.get("ask"))
        spread_price = _as_float(bar.get("spread_price"))
        if spread_price is None and bid is not None and ask is not None:
            spread_price = ask - bid
        # Offline bars use source to distinguish TSV loads from synthetic data.
        source = _source(bar, default="tsv")
        rows.append(
            (
                clean_symbol,
                ts_server,
                ts_server,
                _as_float(bar.get("open")),
                _as_float(bar.get("high")),
                _as_float(bar.get("low")),
                _as_float(bar.get("close")),
                _as_float(bar.get("volume")),
                spread_price,
                _as_float(bar.get("spread_points")),
                source,
                ea_version or _as_text(bar.get("ea_version")),
                git_sha or _as_text(bar.get("git_sha")),
            )
        )
    return _insert_rows(con, rows)


def ingest_bidask_snapshots(
    con: duckdb.DuckDBPyConnection,
    snapshots: list[dict],
    *,
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert bid/ask snapshots, ignoring duplicate symbol/timestamp/source rows."""
    init_prices_schema(con)
    if not snapshots:
        return 0
    ea_version, git_sha = _release_defaults(ea_version, git_sha)
    ts_local = int(time.time())
    rows = []
    for snapshot in snapshots:
        symbol = _as_text(snapshot.get("symbol"))
        ts_server = _as_int(snapshot.get("ts")) or _as_int(snapshot.get("ts_server"))
        bid = _as_float(snapshot.get("bid"))
        ask = _as_float(snapshot.get("ask"))
        if not symbol or ts_server is None or bid is None or ask is None:
            continue
        rows.append(
            (
                symbol,
                ts_server,
                _as_int(snapshot.get("ts_local")) or ts_local,
                None,
                None,
                None,
                None,
                None,
                ask - bid,
                _as_float(snapshot.get("spread_points")),
                "status_snapshot",
                ea_version or _as_text(snapshot.get("ea_version")),
                git_sha or _as_text(snapshot.get("git_sha")),
            )
        )
    return _insert_rows(con, rows)


def load_tsv_bars(path: Path) -> Iterator[dict]:
    """Yield OHLCV or OHLCV+bid/ask bar dictionaries from a no-header TSV file."""
    path = Path(path)
    layout: tuple[str, ...] | None = None
    with path.open(errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for line_no, raw_row in enumerate(reader, start=1):
            row = [cell.strip() for cell in raw_row]
            if not row or all(cell == "" for cell in row):
                continue
            if layout is None:
                layout = _detect_tsv_layout(row, path, line_no)
            if len(row) != len(layout):
                raise ValueError(
                    f"{path}:{line_no}: expected {len(layout)} TSV columns, got {len(row)}"
                )
            bar: dict[str, Any] = {"source": f"tsv:{path}"}
            for key, value in zip(layout, row):
                if key == "time":
                    bar[key] = value
                else:
                    bar[key] = _parse_float(value, path, line_no, key)
            yield bar


def load_status_bidask(common_dir: Path) -> list[dict]:
    """Read finrobot_status.json and return one bid/ask snapshot per symbol."""
    status_path = Path(common_dir) / "finrobot_status.json"
    if not status_path.exists() or not status_path.stat().st_size:
        return []
    try:
        status = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    ts_server = _as_int(status.get("ts")) or _as_int(status.get("ts_server"))
    if ts_server is None:
        return []
    snapshots: list[dict] = []
    symbols = status.get("symbols")
    if not isinstance(symbols, list):
        return []
    for symbol_row in symbols:
        if not isinstance(symbol_row, dict):
            continue
        symbol = _as_text(symbol_row.get("symbol"))
        bid = _as_float(symbol_row.get("bid"))
        ask = _as_float(symbol_row.get("ask"))
        if not symbol or bid is None or ask is None:
            continue
        snapshots.append(
            {
                "symbol": symbol,
                "ts": ts_server,
                "bid": bid,
                "ask": ask,
                "spread_points": _as_float(symbol_row.get("spread_points")),
            }
        )
    return snapshots


def generate_synthetic_bars(
    symbol: str,
    n_bars: int,
    *,
    start_ts: int | None = None,
    interval_seconds: int = 60,
    base_price: float = 2000.0,
    volatility: float = 0.001,
    drift: float = 0.0,
    base_spread: float = 0.05,
    seed: int | None = 42,
) -> list[dict]:
    """Generate a deterministic random-walk bar series for tests and backfills."""
    if n_bars <= 0:
        return []
    rng = random.Random(seed)
    ts = DEFAULT_SYNTHETIC_START_TS if start_ts is None else int(start_ts)
    price = float(base_price)
    bars: list[dict] = []
    for index in range(n_bars):
        open_price = max(price, 0.000001)
        move = rng.gauss(drift, volatility)
        close_price = max(open_price * (1.0 + move), 0.000001)
        wick_high = abs(rng.gauss(0.0, max(volatility, 0.0) / 2.0))
        wick_low = abs(rng.gauss(0.0, max(volatility, 0.0) / 2.0))
        high = max(open_price, close_price) * (1.0 + wick_high)
        low = max(min(open_price, close_price) * (1.0 - wick_low), 0.000001)
        bars.append(
            {
                "symbol": symbol,
                "time": ts + (index * int(interval_seconds)),
                "open": round(open_price, 6),
                "high": round(max(high, low), 6),
                "low": round(min(high, low), 6),
                "close": round(close_price, 6),
                "volume": 1.0,
                "spread_price": float(base_spread),
                "source": "synthetic",
            }
        )
        price = close_price
    return bars


def _insert_rows(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    before = _count(con)
    stage = f"_prices_stage_{uuid.uuid4().hex}"
    con.execute(
        f"""
        CREATE TEMP TABLE {stage} (
          symbol TEXT,
          ts_server INTEGER,
          ts_local INTEGER,
          open DOUBLE,
          high DOUBLE,
          low DOUBLE,
          close DOUBLE,
          volume DOUBLE,
          spread_price DOUBLE,
          spread_points DOUBLE,
          source TEXT,
          ea_version TEXT,
          git_sha TEXT
        )
        """
    )
    try:
        con.executemany(
            f"""
            INSERT INTO {stage} (
              symbol, ts_server, ts_local, open, high, low, close, volume,
              spread_price, spread_points, source, ea_version, git_sha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.execute(
            f"""
            INSERT INTO prices (
              symbol, ts_server, ts_local, open, high, low, close, volume,
              spread_price, spread_points, source, ea_version, git_sha
            )
            SELECT
              symbol, ts_server, ts_local, open, high, low, close, volume,
              spread_price, spread_points, source, ea_version, git_sha
            FROM {stage}
            ON CONFLICT DO NOTHING
            """
        )
    finally:
        con.execute(f"DROP TABLE IF EXISTS {stage}")
    return _count(con) - before


def _detect_tsv_layout(row: list[str], path: Path, line_no: int) -> tuple[str, ...]:
    if len(row) == len(TSV_COLUMNS):
        return TSV_COLUMNS
    if len(row) == len(TSV_BIDASK_COLUMNS):
        return TSV_BIDASK_COLUMNS
    raise ValueError(f"{path}:{line_no}: unsupported TSV column count {len(row)}")


def _parse_float(value: str, path: Path, line_no: int, field: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{path}:{line_no}: {field} should be numeric, got {value!r}") from exc


def _release_defaults(
    ea_version: str | None,
    git_sha: str | None,
) -> tuple[str | None, str | None]:
    if ea_version is not None and git_sha is not None:
        return ea_version, git_sha
    manifest = load_release_manifest()
    return (
        ea_version or _as_text(manifest.get("ea_version")),
        git_sha or _as_text(manifest.get("git_sha")),
    )


def _row_ts(row: dict) -> int | None:
    return _as_int(row.get("ts_server")) or _as_int(row.get("ts")) or _parse_time(row.get("time"))


def _parse_time(value: Any) -> int | None:
    text = _as_text(value)
    if not text:
        return None
    parsed = _as_int(text)
    if parsed is not None:
        return parsed
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return None


def _source(row: dict, default: str) -> str:
    return _as_text(row.get("source")) or default


def _count(con: duckdb.DuckDBPyConnection) -> int:
    return int(con.execute("SELECT count(*) FROM prices").fetchone()[0])


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
