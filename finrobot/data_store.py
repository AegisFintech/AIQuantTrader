from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAREHOUSE = ROOT / "data" / "finrobot.duckdb"


def load_release_manifest(path: Path | None = None) -> dict:
    """Read state/mt5/RELEASE.json if it exists; return empty dict otherwise."""
    path = path or (ROOT / "state" / "mt5" / "RELEASE.json")
    if not path.exists() or not path.stat().st_size:
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def release_defaults(manifest: dict | None = None) -> tuple[str | None, str | None]:
    """Return (ea_version, git_sha) from a release manifest, or (None, None)."""
    if manifest is None:
        manifest = load_release_manifest()
    v = manifest.get("ea_version")
    s = manifest.get("git_sha")
    return (v or None, s or None)


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open the local DuckDB warehouse, creating its parent directory."""
    db_path = path or DEFAULT_WAREHOUSE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the warehouse tables if they do not already exist."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS status (
          ts_server INTEGER NOT NULL,
          ts_local  INTEGER NOT NULL,
          account_login BIGINT,
          server TEXT,
          balance DOUBLE,
          equity DOUBLE,
          margin DOUBLE,
          free_margin DOUBLE,
          positions_count INTEGER,
          money_management JSON,
          symbols JSON,
          ea_version TEXT,
          git_sha TEXT,
          PRIMARY KEY (ts_server)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
          ts_server INTEGER NOT NULL,
          ts_local  INTEGER NOT NULL,
          ticket BIGINT NOT NULL,
          symbol TEXT,
          side TEXT,
          volume DOUBLE,
          open_price DOUBLE,
          current_price DOUBLE,
          profit DOUBLE,
          sl DOUBLE,
          tp DOUBLE,
          comment TEXT,
          ea_version TEXT,
          git_sha TEXT,
          PRIMARY KEY (ticket, ts_server)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS deals (
          ts_server INTEGER NOT NULL,
          ts_local  INTEGER NOT NULL,
          ticket BIGINT NOT NULL,
          "order" BIGINT,
          position_id BIGINT,
          symbol TEXT,
          entry INTEGER,
          type INTEGER,
          volume DOUBLE,
          price DOUBLE,
          profit DOUBLE,
          commission DOUBLE,
          swap DOUBLE,
          comment TEXT,
          ea_version TEXT,
          git_sha TEXT,
          PRIMARY KEY (ticket)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS acks (
          ts_server INTEGER NOT NULL,
          ts_local  INTEGER NOT NULL,
          command_id BIGINT NOT NULL,
          status TEXT,
          message TEXT,
          symbol TEXT,
          side TEXT,
          volume DOUBLE,
          price DOUBLE,
          source TEXT,
          ea_version TEXT,
          git_sha TEXT,
          PRIMARY KEY (command_id, ts_server)
        )
        """
    )


def ingest_status(
    con: duckdb.DuckDBPyConnection,
    status: dict,
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert one status heartbeat row, ignoring an existing ts_server."""
    ts_server = _as_int(_first(status, "ts", "ts_server"))
    if ts_server is None or _exists(con, "SELECT 1 FROM status WHERE ts_server = ?", [ts_server]):
        return 0
    if ea_version is None or git_sha is None:
        v, s = release_defaults()
        ea_version = ea_version or v
        git_sha = git_sha or s
    con.execute(
        """
        INSERT INTO status (
          ts_server, ts_local, account_login, server, balance, equity, margin,
          free_margin, positions_count, money_management, symbols, ea_version, git_sha
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), CAST(? AS JSON), ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            ts_server,
            _local_ts(status),
            _as_int(_first(status, "login", "account_login")),
            _as_text(status.get("server")),
            _as_float(status.get("balance")),
            _as_float(status.get("equity")),
            _as_float(status.get("margin")),
            _as_float(status.get("free_margin")),
            _as_int(_first(status, "positions", "positions_count")),
            _json_value(status.get("money_management")),
            _json_value(status.get("symbols")),
            ea_version or _as_text(status.get("ea_version")),
            git_sha or _as_text(status.get("git_sha")),
        ],
    )
    return 1


def ingest_positions(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict],
    ts_server: int | None = None,
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert position snapshot rows, ignoring duplicate ticket/timestamp pairs."""
    inserted = 0
    ts_local = int(time.time())
    if ea_version is None or git_sha is None:
        v, s = release_defaults()
        ea_version = ea_version or v
        git_sha = git_sha or s
    for row in rows:
        server_ts = _as_int(ts_server) or _row_ts(row)
        ticket = _as_int(row.get("ticket"))
        if server_ts is None or ticket is None:
            continue
        if _exists(
            con,
            "SELECT 1 FROM positions WHERE ticket = ? AND ts_server = ?",
            [ticket, server_ts],
        ):
            continue
        con.execute(
            """
            INSERT INTO positions (
              ts_server, ts_local, ticket, symbol, side, volume, open_price,
              current_price, profit, sl, tp, comment, ea_version, git_sha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                server_ts,
                _as_int(row.get("ts_local")) or ts_local,
                ticket,
                _as_text(row.get("symbol")),
                _as_text(_first(row, "side", "type")),
                _as_float(row.get("volume")),
                _as_float(row.get("open_price")),
                _as_float(row.get("current_price")),
                _as_float(row.get("profit")),
                _as_float(row.get("sl")),
                _as_float(row.get("tp")),
                _as_text(row.get("comment")),
                ea_version or _as_text(row.get("ea_version")),
                git_sha or _as_text(row.get("git_sha")),
            ],
        )
        inserted += 1
    return inserted


def ingest_deals(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict],
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert deal history rows, ignoring duplicate deal tickets."""
    inserted = 0
    ts_local = int(time.time())
    if ea_version is None or git_sha is None:
        v, s = release_defaults()
        ea_version = ea_version or v
        git_sha = git_sha or s
    for row in rows:
        ticket = _as_int(row.get("ticket"))
        server_ts = _row_ts(row)
        if ticket is None or server_ts is None:
            continue
        if _exists(con, "SELECT 1 FROM deals WHERE ticket = ?", [ticket]):
            continue
        con.execute(
            """
            INSERT INTO deals (
              ts_server, ts_local, ticket, "order", position_id, symbol, entry,
              type, volume, price, profit, commission, swap, comment, ea_version, git_sha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                server_ts,
                _as_int(row.get("ts_local")) or ts_local,
                ticket,
                _as_int(row.get("order")),
                _as_int(row.get("position_id")),
                _as_text(row.get("symbol")),
                _as_int(row.get("entry")),
                _as_int(row.get("type")),
                _as_float(row.get("volume")),
                _as_float(row.get("price")),
                _as_float(row.get("profit")),
                _as_float(row.get("commission")),
                _as_float(row.get("swap")),
                _as_text(row.get("comment")),
                ea_version or _as_text(row.get("ea_version")),
                git_sha or _as_text(row.get("git_sha")),
            ],
        )
        inserted += 1
    return inserted


def ingest_acks(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict],
    ea_version: str | None = None,
    git_sha: str | None = None,
) -> int:
    """Insert acknowledgement rows, ignoring duplicate command/timestamp pairs."""
    inserted = 0
    ts_local = int(time.time())
    if ea_version is None or git_sha is None:
        v, s = release_defaults()
        ea_version = ea_version or v
        git_sha = git_sha or s
    for row in rows:
        command_id = _as_int(_first(row, "command_id", "id"))
        server_ts = _row_ts(row)
        if command_id is None or server_ts is None:
            continue
        if _exists(
            con,
            "SELECT 1 FROM acks WHERE command_id = ? AND ts_server = ?",
            [command_id, server_ts],
        ):
            continue
        status = _as_text(row.get("status"))
        con.execute(
            """
            INSERT INTO acks (
              ts_server, ts_local, command_id, status, message, symbol, side,
              volume, price, source, ea_version, git_sha
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                server_ts,
                _as_int(row.get("ts_local")) or ts_local,
                command_id,
                status,
                _as_text(_first(row, "message", "detail")),
                _as_text(row.get("symbol")),
                _as_text(row.get("side")),
                _as_float(row.get("volume")),
                _as_float(row.get("price")),
                _ack_source(status),
                ea_version or _as_text(row.get("ea_version")),
                git_sha or _as_text(row.get("git_sha")),
            ],
        )
        inserted += 1
    return inserted


def query_summary(con: duckdb.DuckDBPyConnection) -> dict:
    """Return warehouse row counts and the latest status server timestamp."""
    return {
        "status": _count(con, "status"),
        "positions": _count(con, "positions"),
        "deals": _count(con, "deals"),
        "acks": _count(con, "acks"),
        "latest_status_ts_server": con.execute(
            "SELECT max(ts_server) FROM status"
        ).fetchone()[0],
    }


def _exists(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> bool:
    return con.execute(sql, params).fetchone() is not None


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _local_ts(row: dict) -> int:
    return _as_int(row.get("ts_local")) or int(time.time())


def _row_ts(row: dict) -> int | None:
    return _as_int(_first(row, "ts_server", "ts")) or _parse_time(row.get("time"))


def _first(row: dict, *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _json_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return json.dumps(value, sort_keys=True)


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


def _parse_time(value: Any) -> int | None:
    text = _as_text(value)
    if not text:
        return None
    parsed = _as_int(text)
    if parsed is not None:
        return parsed
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def _ack_source(status: str | None) -> str | None:
    if not status:
        return None
    upper = status.upper()
    if upper.startswith("AUTO_") or upper.startswith("RISK_"):
        return "AUTO"
    return "COMMAND"
