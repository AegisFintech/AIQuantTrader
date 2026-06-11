from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mt5_trade_report import money  # noqa: E402


MIN_VALID_TS = 1577836800  # 2020-01-01 00:00:00 UTC
PRICE_MIN_VALID_TS = 1420070400  # 2015-01-01 00:00:00 UTC
# MT5 broker servers run their own clocks; ICMarkets in particular has been
# observed ~170 minutes ahead of the host. Allow 15 min of skew as the
# normal case and surface anything beyond. Override via env var
# `FINROBOT_VALIDATOR_FUTURE_TOLERANCE_SECONDS` if you need a different bound.
import os

MAX_FUTURE_SECONDS = int(
    os.getenv("FINROBOT_VALIDATOR_FUTURE_TOLERANCE_SECONDS", "900")
)
KNOWN_ACK_STATUSES = {
    "OK",
    "ERROR",
    "REJECTED",
    "RISK_CLOSED",
    "RISK_CLOSE_FAILED",
    "AUTO_FILLED",
    "AUTO_REJECTED",
}
TRADE_ACK_STATUSES = {
    "REJECTED",
    "RISK_CLOSED",
    "RISK_CLOSE_FAILED",
    "AUTO_FILLED",
    "AUTO_REJECTED",
}


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Issue:
    severity: Severity
    check: str
    location: str
    detail: str
    suggestion: str = ""


def validate_status(row: dict) -> list[Issue]:
    """Validate one row from the status table."""
    row = _safe_row(row)
    issues: list[Issue] = []
    _validate_ts(row, "status", issues, required_local=True)
    for field in ("balance", "equity", "margin", "free_margin"):
        value = _coerce_float(row, field, "status", issues)
        if value is None:
            continue
        if value < 0:
            issues.append(
                _warning(
                    "status_numeric_negative",
                    f"status:{field}",
                    f"{field} is negative ({value})",
                )
            )
    mm = _json_value(row.get("money_management"), "status:money_management", issues)
    if isinstance(mm, Mapping):
        _validate_money_management(mm, issues)
    symbols = _json_value(row.get("symbols"), "status:symbols", issues)
    if isinstance(symbols, list):
        _validate_symbols(symbols, issues)
    return issues


def validate_position(row: dict) -> list[Issue]:
    """Validate one row from the positions table."""
    row = _safe_row(row)
    issues: list[Issue] = []
    ticket = _coerce_int(row, "ticket", "positions", issues, required=True)
    if ticket is not None and ticket <= 0:
        issues.append(
            _error(
                "position_ticket_invalid",
                "positions:ticket",
                f"ticket must be greater than 0, got {ticket}",
            )
        )
    _validate_ts(row, "positions", issues)
    volume = _coerce_float(row, "volume", "positions", issues)
    if volume is not None and volume <= 0:
        issues.append(
            _warning(
                "position_volume_nonpositive",
                _row_location("positions", row),
                f"volume should be greater than 0, got {volume}",
            )
        )
    price_values: dict[str, float | None] = {}
    for field in ("open_price", "current_price", "sl", "tp"):
        value = _coerce_float(row, field, "positions", issues)
        price_values[field] = value
        if value is not None and value < 0:
            issues.append(
                _warning(
                    "position_price_negative",
                    f"{_row_location('positions', row)}:{field}",
                    f"{field} should be greater than or equal to 0, got {value}",
                )
            )
    sl = price_values.get("sl") or 0.0
    tp = price_values.get("tp") or 0.0
    if sl == 0.0 and tp == 0.0:
        issues.append(
            _warning(
                "position_unprotected",
                _row_location("positions", row),
                "position has both sl and tp set to 0",
                "Managed positions should have at least one protective exit.",
            )
        )
    side = _text(row.get("side"))
    if side is None or side.upper() not in {"BUY", "SELL"}:
        issues.append(
            _warning(
                "position_side_unknown",
                _row_location("positions", row),
                f"side should be BUY or SELL, got {row.get('side')!r}",
            )
        )
    if not _non_empty_text(row.get("symbol")):
        issues.append(
            _warning(
                "position_symbol_missing",
                _row_location("positions", row),
                "symbol should be a non-empty string",
            )
        )
    return issues


def validate_deal(row: dict) -> list[Issue]:
    """Validate one row from the deals table."""
    row = _safe_row(row)
    issues: list[Issue] = []
    ticket = _coerce_int(row, "ticket", "deals", issues, required=True)
    if ticket is not None and ticket <= 0:
        issues.append(
            _error(
                "deal_ticket_invalid",
                "deals:ticket",
                f"ticket must be greater than 0, got {ticket}",
            )
        )
    _validate_ts(row, "deals", issues)
    volume = _coerce_float(row, "volume", "deals", issues)
    if volume is not None and volume <= 0:
        issues.append(
            _warning(
                "deal_volume_nonpositive",
                _row_location("deals", row),
                f"volume should be greater than 0, got {volume}",
            )
        )
    price = _coerce_float(row, "price", "deals", issues)
    if price is not None and price < 0:
        issues.append(
            _warning(
                "deal_price_negative",
                _row_location("deals", row),
                f"price should be greater than or equal to 0, got {price}",
            )
        )
    pnl_parts = [
        _coerce_float(row, field, "deals", issues)
        for field in ("profit", "commission", "swap")
    ]
    if all(value is not None for value in pnl_parts):
        pnl = sum(value for value in pnl_parts if value is not None)
        if not math.isfinite(pnl):
            issues.append(
                _warning(
                    "deal_pnl_nonfinite",
                    _row_location("deals", row),
                    "profit + commission + swap should be finite",
                )
            )
    entry = _coerce_int(row, "entry", "deals", issues)
    if entry not in {0, 1, 2, 3}:
        issues.append(
            _warning(
                "deal_entry_unknown",
                _row_location("deals", row),
                f"entry should be 0, 1, 2, or 3, got {row.get('entry')!r}",
            )
        )
    if not _non_empty_text(row.get("symbol")):
        issues.append(
            _warning(
                "deal_symbol_missing",
                _row_location("deals", row),
                "symbol should be a non-empty string",
            )
        )
    comment = row.get("comment")
    if comment not in (None, "") and not _is_ascii_printable(comment):
        issues.append(
            _warning(
                "deal_comment_nonprintable",
                _row_location("deals", row),
                "comment should contain only ASCII-printable characters",
            )
        )
    return issues


def validate_ack(row: dict) -> list[Issue]:
    """Validate one row from the acks table."""
    row = _safe_row(row)
    issues: list[Issue] = []
    command_id = _coerce_int(row, "command_id", "acks", issues, required=True)
    if command_id is not None and command_id <= 0:
        issues.append(
            _error(
                "ack_command_id_invalid",
                "acks:command_id",
                f"command_id must be greater than 0, got {command_id}",
            )
        )
    _validate_ts(row, "acks", issues)
    status = _text(row.get("status"))
    status_upper = status.upper() if status else ""
    if status_upper not in KNOWN_ACK_STATUSES:
        issues.append(
            _warning(
                "ack_status_unknown",
                _row_location("acks", row, "command_id"),
                f"status is not in the known set: {row.get('status')!r}",
            )
        )
    source = _text(row.get("source"))
    if source is not None and source.upper() not in {"AUTO", "COMMAND"}:
        issues.append(
            _warning(
                "ack_source_unknown",
                _row_location("acks", row, "command_id"),
                f"source should be AUTO or COMMAND, got {row.get('source')!r}",
            )
        )
    if status_upper in TRADE_ACK_STATUSES and not _non_empty_text(row.get("symbol")):
        issues.append(
            _warning(
                "ack_trade_symbol_missing",
                _row_location("acks", row, "command_id"),
                f"symbol should be present for trade status {status_upper}",
            )
        )
    return issues


def validate_price(row: dict) -> list[Issue]:
    """Validate one row from the prices table."""
    row = _safe_row(row)
    issues: list[Issue] = []
    location = _price_location(row)

    if not _non_empty_text(row.get("symbol")):
        issues.append(
            _error(
                "price_symbol_missing",
                "prices:symbol",
                "missing required field symbol",
            )
        )
    ts_server = _coerce_int(row, "ts_server", "prices", issues, required=True)
    _coerce_int(row, "ts_local", "prices", issues, required=True)
    source = _text(row.get("source"))
    if source is None:
        issues.append(
            _error(
                "price_source_missing",
                f"{location}:source",
                "missing required field source",
            )
        )
    elif source not in {"tsv", "status_snapshot", "synthetic"} and not source.startswith("tsv:"):
        issues.append(
            _warning(
                "price_source_unknown",
                f"{location}:source",
                f"source should be tsv, status_snapshot, synthetic, or tsv:<path>, got {source!r}",
            )
        )

    if ts_server is not None:
        now = int(time.time())
        if ts_server > now + MAX_FUTURE_SECONDS:
            issues.append(
                _warning(
                    "price_ts_server_future",
                    location,
                    f"ts_server is more than {MAX_FUTURE_SECONDS} seconds in the future",
                )
            )
        if ts_server < PRICE_MIN_VALID_TS:
            issues.append(
                _warning(
                    "price_ts_server_too_old",
                    location,
                    "ts_server is before 2015-01-01",
                )
            )

    ohlc_fields = ("open", "high", "low", "close")
    present = [field for field in ohlc_fields if row.get(field) not in (None, "")]
    ohlc: dict[str, float | None] = {
        field: _coerce_float(row, field, "prices", issues) for field in ohlc_fields
    }
    if present and len(present) != len(ohlc_fields):
        issues.append(
            _error(
                "price_ohlc_partial",
                location,
                f"OHLC fields must be all present or all absent; present={present}",
            )
        )
    close = ohlc["close"]
    if close is not None and close <= 0:
        issues.append(
            _error(
                "price_close_nonpositive",
                f"{location}:close",
                f"close must be greater than 0, got {close}",
            )
        )
    if present and all(ohlc[field] is not None for field in ohlc_fields):
        open_price = ohlc["open"]
        high = ohlc["high"]
        low = ohlc["low"]
        close_price = ohlc["close"]
        assert open_price is not None
        assert high is not None
        assert low is not None
        assert close_price is not None
        _check_ohlc_bounds(location, open_price, high, low, close_price, issues)

    spread_price = _coerce_float(row, "spread_price", "prices", issues)
    if spread_price is not None and spread_price < 0:
        issues.append(
            _warning(
                "price_spread_price_negative",
                f"{location}:spread_price",
                f"spread_price should be greater than or equal to 0, got {spread_price}",
            )
        )
    spread_points = _coerce_float(row, "spread_points", "prices", issues)
    if spread_points is not None and spread_points < 0:
        issues.append(
            _warning(
                "price_spread_points_negative",
                f"{location}:spread_points",
                f"spread_points should be greater than or equal to 0, got {spread_points}",
            )
        )
    return issues


def reconcile_positions_vs_deals(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Check open positions have entry deals and older entry deals have closes."""
    issues: list[Issue] = []
    open_positions = con.execute(
        """
        SELECT p.ticket
        FROM (SELECT DISTINCT ticket FROM positions) p
        WHERE NOT EXISTS (
          SELECT 1
          FROM deals d
          WHERE d.entry = 0
            AND (d.ticket = p.ticket OR d.position_id = p.ticket)
        )
        ORDER BY p.ticket
        """
    ).fetchall()
    for (ticket,) in open_positions:
        issues.append(
            _warning(
                "position_entry_deal_missing",
                f"positions:ticket={ticket}",
                "open position has no matching entry-in deal",
            )
        )

    cutoff = int(time.time() - 3600)
    orphan_entries = con.execute(
        """
        SELECT ticket, position_id, symbol, ts_server
        FROM deals e
        WHERE e.entry = 0
          AND e.ts_server < ?
          AND NOT EXISTS (
            SELECT 1
            FROM deals c
            WHERE c.entry IN (1, 2)
              AND (
                (e.position_id IS NOT NULL AND c.position_id = e.position_id)
                OR (e.position_id IS NULL AND c.position_id IS NULL AND c.ticket = e.ticket)
              )
          )
        ORDER BY e.ts_server, e.ticket
        """,
        [cutoff],
    ).fetchall()
    for ticket, position_id, symbol, ts_server in orphan_entries:
        hint = f"deals:ticket={ticket}"
        if position_id is not None:
            hint += f",position_id={position_id}"
        issues.append(
            _warning(
                "deal_entry_close_missing",
                hint,
                (
                    "entry-in deal older than 1 hour has no matching close "
                    f"(symbol={symbol or '?'}, ts_server={ts_server})"
                ),
            )
        )
    return issues


def reconcile_status_positions_count(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Compare latest status positions_count to positions at the same timestamp."""
    row = con.execute(
        """
        SELECT ts_server, positions_count
        FROM status
        ORDER BY ts_server DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return []
    ts_server, positions_count = row
    actual = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT ticket FROM positions WHERE ts_server = ?)",
        [ts_server],
    ).fetchone()[0]
    if positions_count != actual:
        return [
            _warning(
                "status_positions_count_mismatch",
                f"status:ts_server={ts_server}",
                f"positions_count={positions_count} but distinct open positions={actual}",
            )
        ]
    return []


def reconcile_status_equity_vs_balance(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Check latest status equity and balance are finite and broadly aligned."""
    row = con.execute(
        """
        SELECT ts_server, equity, balance
        FROM status
        ORDER BY ts_server DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return []
    ts_server, equity, balance = row
    if not _finite_number(equity) or not _finite_number(balance):
        return [
            _warning(
                "status_equity_balance_nonfinite",
                f"status:ts_server={ts_server}",
                f"equity and balance should be finite, got equity={equity!r}, balance={balance!r}",
            )
        ]
    if float(balance) == 0.0:
        if float(equity) == 0.0:
            return []
        return [
            _warning(
                "status_equity_balance_ratio",
                f"status:ts_server={ts_server}",
                f"balance is 0 but equity is {equity}",
            )
        ]
    ratio = float(equity) / float(balance)
    if ratio < 0.5 or ratio > 2.0:
        return [
            _warning(
                "status_equity_balance_ratio",
                f"status:ts_server={ts_server}",
                f"equity/balance ratio {ratio:.4f} is outside 0.5-2.0",
            )
        ]
    return []


def reconcile_prices_against_positions(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Check latest open positions are aligned with the latest available price bar."""
    if not _table_exists(con, "positions") or not _table_exists(con, "prices"):
        return []
    latest_ts = con.execute("SELECT max(ts_server) FROM positions").fetchone()[0]
    if latest_ts is None:
        return []
    rows = _fetch_dicts(
        con,
        """
        SELECT ticket, symbol, ts_server, open_price
        FROM positions
        WHERE ts_server = ?
        ORDER BY ticket
        """,
        [latest_ts],
    )
    issues: list[Issue] = []
    for row in rows:
        symbol = _text(row.get("symbol"))
        open_price = _float_value(row.get("open_price"))
        position_ts = _int_value(row.get("ts_server"))
        if not symbol or open_price is None or position_ts is None:
            continue
        price = con.execute(
            """
            SELECT ts_server, close, source
            FROM prices
            WHERE symbol = ?
              AND close IS NOT NULL
              AND ts_server <= ?
            ORDER BY ts_server DESC, source
            LIMIT 1
            """,
            [symbol, position_ts],
        ).fetchone()
        location = f"positions:ticket={row.get('ticket')}"
        if price is None:
            issues.append(
                _warning(
                    "position_price_reference_missing",
                    location,
                    f"no price bar found for {symbol} at or before ts_server={position_ts}",
                )
            )
            continue
        price_ts, close, source = price
        close_value = _float_value(close)
        if close_value is None or close_value <= 0:
            continue
        distance = abs(float(open_price) - close_value) / close_value
        if distance > 0.005:
            issues.append(
                _warning(
                    "position_price_mismatch",
                    location,
                    (
                        f"open_price={float(open_price):.6f} is {distance:.2%} from "
                        f"latest {symbol} close={close_value:.6f} "
                        f"(price_ts_server={price_ts}, source={source})"
                    ),
                )
            )
    return issues


def reconcile_deals_pnl_by_symbol(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Surface symbols with many closed deals missing strategy comments."""
    rows = _fetch_dicts(
        con,
        """
        SELECT symbol, profit, commission, swap, comment
        FROM deals
        WHERE entry IN (1, 2, 3)
        """,
    )
    totals: dict[str, float] = {}
    missing_comments: dict[str, int] = {}
    for row in rows:
        symbol = _text(row.get("symbol")) or "?"
        totals[symbol] = totals.get(symbol, 0.0) + (
            money(row.get("profit")) + money(row.get("commission")) + money(row.get("swap"))
        )
        if not _non_empty_text(row.get("comment")):
            missing_comments[symbol] = missing_comments.get(symbol, 0) + 1
    issues: list[Issue] = []
    for symbol, missing_count in sorted(missing_comments.items()):
        if missing_count > 10:
            issues.append(
                _warning(
                    "deals_comment_attribution_missing",
                    f"deals:symbol={symbol}",
                    (
                        f"{missing_count} closed deals are missing comments; "
                        f"total_pnl={totals.get(symbol, 0.0):.2f}"
                    ),
                    "Preserve entry comments so strategy attribution stays available.",
                )
            )
    return issues


def validate_warehouse(con: duckdb.DuckDBPyConnection) -> list[Issue]:
    """Run row-level and reconciliation validation across the warehouse."""
    issues: list[Issue] = []
    for row in _fetch_dicts(con, "SELECT * FROM status ORDER BY ts_server"):
        issues.extend(validate_status(row))
    for row in _fetch_dicts(con, "SELECT * FROM positions ORDER BY ts_server, ticket"):
        issues.extend(validate_position(row))
    for row in _fetch_dicts(con, "SELECT * FROM deals ORDER BY ts_server, ticket"):
        issues.extend(validate_deal(row))
    for row in _fetch_dicts(con, "SELECT * FROM acks ORDER BY ts_server, command_id"):
        issues.extend(validate_ack(row))
    if _table_exists(con, "prices"):
        for row in _fetch_dicts(con, "SELECT * FROM prices ORDER BY ts_server, symbol, source"):
            issues.extend(validate_price(row))
    for check in (
        reconcile_positions_vs_deals,
        reconcile_status_positions_count,
        reconcile_status_equity_vs_balance,
        reconcile_prices_against_positions,
        reconcile_deals_pnl_by_symbol,
    ):
        issues.extend(check(con))
    return issues


def _validate_money_management(mm: Mapping[str, Any], issues: list[Issue]) -> None:
    if "loss_limit_reached" not in mm:
        issues.append(
            _warning(
                "status_money_management_missing",
                "status:money_management.loss_limit_reached",
                "money_management is missing loss_limit_reached",
            )
        )
    else:
        reached = _int_value(mm.get("loss_limit_reached"))
        if reached not in {0, 1}:
            issues.append(
                _warning(
                    "status_money_management_invalid",
                    "status:money_management.loss_limit_reached",
                    f"loss_limit_reached should be 0 or 1, got {mm.get('loss_limit_reached')!r}",
                )
            )
    for field in ("daily_risk_per_trade_fraction", "daily_loss_limit_fraction"):
        if field not in mm:
            issues.append(
                _warning(
                    "status_money_management_missing",
                    f"status:money_management.{field}",
                    f"money_management is missing {field}",
                )
            )
            continue
        value = _float_value(mm.get(field))
        if value is None or not math.isfinite(value) or value < 0 or value > 1:
            issues.append(
                _warning(
                    "status_money_management_invalid",
                    f"status:money_management.{field}",
                    f"{field} should be in [0, 1], got {mm.get(field)!r}",
                )
            )


def _validate_symbols(symbols: list[Any], issues: list[Issue]) -> None:
    for index, symbol_row in enumerate(symbols):
        location = f"status:symbols[{index}]"
        if not isinstance(symbol_row, Mapping):
            issues.append(
                _warning(
                    "status_symbol_invalid",
                    location,
                    "symbols entry should be an object",
                )
            )
            continue
        if not _non_empty_text(symbol_row.get("symbol")):
            issues.append(
                _warning(
                    "status_symbol_missing",
                    f"{location}.symbol",
                    "symbol entry is missing a non-empty symbol",
                )
            )
        if not isinstance(symbol_row.get("last_signal"), str):
            issues.append(
                _warning(
                    "status_symbol_last_signal_missing",
                    f"{location}.last_signal",
                    "symbol entry is missing last_signal string",
                )
            )


def _validate_ts(
    row: Mapping[str, Any],
    table: str,
    issues: list[Issue],
    required_local: bool = False,
) -> None:
    ts_server = _coerce_int(row, "ts_server", table, issues, required=True)
    if required_local:
        _coerce_int(row, "ts_local", table, issues, required=True)
    if ts_server is None:
        return
    if ts_server <= MIN_VALID_TS or ts_server > int(time.time()) + MAX_FUTURE_SECONDS:
        issues.append(
            _warning(
                f"{_singular(table)}_ts_server_out_of_range",
                f"{table}:ts_server={ts_server}",
                f"ts_server should be after 2020-01-01 and no more than {MAX_FUTURE_SECONDS} seconds in the future (override via FINROBOT_VALIDATOR_FUTURE_TOLERANCE_SECONDS)",
            )
        )


def _fetch_dicts(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list[Any] | None = None,
) -> list[dict]:
    result = con.execute(sql, params or [])
    columns = [desc[0] for desc in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = ?
        LIMIT 1
        """,
        [table],
    ).fetchone()
    return row is not None


def _check_ohlc_bounds(
    location: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    issues: list[Issue],
) -> None:
    failures = []
    if high < low:
        failures.append("high < low")
    if high < open_price:
        failures.append("high < open")
    if high < close:
        failures.append("high < close")
    if low > open_price:
        failures.append("low > open")
    if low > close:
        failures.append("low > close")
    if failures:
        issues.append(
            _warning(
                "price_ohlc_inconsistent",
                location,
                "OHLC bounds are inconsistent: " + ", ".join(failures),
            )
        )


def _safe_row(row: Any) -> dict:
    if isinstance(row, Mapping):
        return dict(row)
    return {}


def _coerce_int(
    row: Mapping[str, Any],
    field: str,
    table: str,
    issues: list[Issue],
    required: bool = False,
) -> int | None:
    if field not in row or row.get(field) in (None, ""):
        if required:
            issues.append(
                _error(
                    f"{_singular(table)}_{field}_missing",
                    f"{table}:{field}",
                    f"missing required field {field}",
                )
            )
        return None
    value = _int_value(row.get(field))
    if value is None:
        severity = Severity.ERROR if required else Severity.WARNING
        issues.append(
            Issue(
                severity=severity,
                check=f"{_singular(table)}_{field}_invalid",
                location=f"{table}:{field}",
                detail=f"{field} should be an integer, got {row.get(field)!r}",
            )
        )
    return value


def _coerce_float(
    row: Mapping[str, Any],
    field: str,
    table: str,
    issues: list[Issue],
) -> float | None:
    if field not in row or row.get(field) in (None, ""):
        return None
    value = _float_value(row.get(field))
    if value is None:
        issues.append(
            _warning(
                f"{_singular(table)}_{field}_invalid",
                f"{table}:{field}",
                f"{field} should be numeric, got {row.get(field)!r}",
            )
        )
        return None
    if not math.isfinite(value):
        issues.append(
            _error(
                f"{_singular(table)}_{field}_nonfinite",
                f"{table}:{field}",
                f"{field} should be finite, got {row.get(field)!r}",
            )
        )
    return value


def _json_value(value: Any, location: str, issues: list[Issue]) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            issues.append(
                _warning(
                    "status_json_invalid",
                    location,
                    "stored JSON value could not be parsed",
                )
            )
            return value
    return value


def _float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number)


def _finite_number(value: Any) -> bool:
    number = _float_value(value)
    return number is not None and math.isfinite(number)


def _is_ascii_printable(value: Any) -> bool:
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            return False
    else:
        text = str(value)
    return all(32 <= ord(ch) <= 126 for ch in text)


def _non_empty_text(value: Any) -> bool:
    return _text(value) is not None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_location(table: str, row: Mapping[str, Any], key: str = "ticket") -> str:
    value = row.get(key)
    if value not in (None, ""):
        return f"{table}:{key}={value}"
    return table


def _price_location(row: Mapping[str, Any]) -> str:
    symbol = row.get("symbol") or "?"
    ts_server = row.get("ts_server") or "?"
    source = row.get("source") or "?"
    return f"prices:symbol={symbol},ts_server={ts_server},source={source}"


def _singular(table: str) -> str:
    return {
        "positions": "position",
        "deals": "deal",
        "acks": "ack",
        "prices": "price",
    }.get(table, table)


def _warning(check: str, location: str, detail: str, suggestion: str = "") -> Issue:
    return Issue(Severity.WARNING, check, location, detail, suggestion)


def _error(check: str, location: str, detail: str, suggestion: str = "") -> Issue:
    return Issue(Severity.ERROR, check, location, detail, suggestion)
