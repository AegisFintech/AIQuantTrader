from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for warehouse tests")

ROOT = Path(__file__).resolve().parents[1]

from finrobot import data_store  # noqa: E402
from finrobot.validators import (  # noqa: E402
    Severity,
    reconcile_positions_vs_deals,
    reconcile_status_equity_vs_balance,
    reconcile_status_positions_count,
    validate_ack,
    validate_deal,
    validate_position,
    validate_status,
    validate_warehouse,
)


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(db)
    data_store.init_schema(connection)
    yield connection
    connection.close()


def test_validate_status_happy_path_empty():
    assert validate_status(_status()) == []


def test_validate_status_old_ts_warns():
    issues = validate_status(_status(ts_server=1514764800))
    assert _has_warning(issues, "status_ts_server_out_of_range")


def test_validate_status_negative_balance_warns():
    issues = validate_status(_status(balance=-1.0))
    assert _has_warning(issues, "status_numeric_negative")


def test_validate_status_money_management_missing_loss_limit_warns():
    status = _status()
    del status["money_management"]["loss_limit_reached"]
    issues = validate_status(status)
    assert _has_warning(issues, "status_money_management_missing")


def test_validate_position_unprotected_warns():
    issues = validate_position(_position(sl="0", tp="0"))
    assert _has_warning(issues, "position_unprotected")


def test_validate_position_unknown_side_warns():
    issues = validate_position(_position(side="LONG"))
    assert _has_warning(issues, "position_side_unknown")


def test_validate_deal_unknown_entry_warns():
    issues = validate_deal(_deal(entry=99))
    assert _has_warning(issues, "deal_entry_unknown")


def test_validate_deal_non_ascii_comment_warns():
    issues = validate_deal(_deal(comment="FinRobot_XAUUSD_\u00e9"))
    assert _has_warning(issues, "deal_comment_nonprintable")


def test_validate_ack_unknown_status_warns_not_error():
    issues = validate_ack(_ack(status="WEIRD_NEW_THING"))
    assert _has_warning(issues, "ack_status_unknown")
    assert not any(issue.severity == Severity.ERROR for issue in issues)


def test_reconcile_positions_vs_deals_position_without_entry_warns(con):
    data_store.ingest_positions(con, [_position(ticket=1001)], ts_server=_now())

    issues = reconcile_positions_vs_deals(con)

    assert _has_warning(issues, "position_entry_deal_missing")


def test_reconcile_positions_vs_deals_old_open_entry_without_close_warns(con):
    old_ts = _now() - 7200
    data_store.ingest_deals(
        con,
        [_deal(ticket=2001, position_id=9001, entry=0, ts_server=old_ts)],
    )

    issues = reconcile_positions_vs_deals(con)

    assert _has_warning(issues, "deal_entry_close_missing")


def test_reconcile_status_positions_count_mismatch_warns(con):
    ts_server = _now()
    data_store.ingest_status(con, _status(ts_server=ts_server, positions_count=2))

    issues = reconcile_status_positions_count(con)

    assert _has_warning(issues, "status_positions_count_mismatch")


def test_reconcile_status_equity_vs_balance_large_ratio_warns(con):
    data_store.ingest_status(con, _status(balance=100.0, equity=1000.0))

    issues = reconcile_status_equity_vs_balance(con)

    assert _has_warning(issues, "status_equity_balance_ratio")


def test_validate_warehouse_end_to_end_mixed_issues(con):
    ts_server = _now()
    bad_status = _status(
        ts_server=ts_server,
        balance=-10.0,
        positions_count=2,
        money_management={
            "daily_risk_per_trade_fraction": 0.001,
            "daily_loss_limit_fraction": 0.01,
        },
        symbols=[{"symbol": "XAUUSD"}],
    )
    data_store.ingest_status(con, bad_status)
    data_store.ingest_positions(
        con,
        [
            _position(ticket=3001, side="BUY", sl="59000", tp="62000"),
            _position(ticket=3002, side="LONG", sl="0", tp="0"),
        ],
        ts_server=ts_server,
    )
    data_store.ingest_deals(
        con,
        [
            _deal(ticket=4001, position_id=3001, entry=0, ts_server=ts_server),
            _deal(ticket=4002, position_id=3001, entry=1, ts_server=ts_server),
            _deal(ticket=4003, position_id=3002, entry=99, ts_server=ts_server),
        ],
    )
    data_store.ingest_acks(con, [_ack(command_id=5001, status="WEIRD_NEW_THING")])

    issues = validate_warehouse(con)

    assert len(issues) >= 5
    assert _has_warning(issues, "status_numeric_negative")
    assert _has_warning(issues, "status_money_management_missing")
    assert _has_warning(issues, "position_side_unknown")
    assert _has_warning(issues, "deal_entry_unknown")
    assert _has_warning(issues, "ack_status_unknown")


def test_cli_smoke_exit_zero_with_warnings_and_missing_exit_two(tmp_path):
    warehouse = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(warehouse)
    try:
        data_store.init_schema(connection)
        data_store.ingest_status(connection, _status(balance=-1.0))
    finally:
        connection.close()

    ok = subprocess.run(
        [sys.executable, "scripts/mt5_validate_warehouse.py", "--warehouse", str(warehouse)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    missing = subprocess.run(
        [
            sys.executable,
            "scripts/mt5_validate_warehouse.py",
            "--warehouse",
            str(tmp_path / "missing.duckdb"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert ok.returncode == 0
    assert "issues: errors=0 warnings=" in ok.stdout
    assert "warehouse=dirty" in ok.stdout
    assert missing.returncode == 2


def test_cli_smoke_exit_one_for_errors_and_json_output(tmp_path):
    warehouse = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(warehouse)
    try:
        data_store.init_schema(connection)
        data_store.ingest_status(connection, _status(balance="nan"))
    finally:
        connection.close()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/mt5_validate_warehouse.py",
            "--warehouse",
            str(warehouse),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(issue["severity"] == "error" for issue in payload)
    assert any(issue["check"] == "status_balance_nonfinite" for issue in payload)


def _has_warning(issues, check: str) -> bool:
    return any(issue.severity == Severity.WARNING and issue.check == check for issue in issues)


def _now() -> int:
    return int(time.time())


def _status(
    ts_server: int | None = None,
    balance=1000.0,
    equity=1000.0,
    positions_count: int = 0,
    money_management: dict | None = None,
    symbols: list[dict] | None = None,
) -> dict:
    now = _now() if ts_server is None else ts_server
    if money_management is None:
        money_management = {
            "loss_limit_reached": 0,
            "daily_risk_per_trade_fraction": 0.001,
            "daily_loss_limit_fraction": 0.01,
        }
    if symbols is None:
        symbols = [{"symbol": "XAUUSD", "last_signal": "no_signal"}]
    return {
        "ts": now,
        "ts_server": now,
        "ts_local": now,
        "balance": balance,
        "equity": equity,
        "margin": 0.0,
        "free_margin": equity,
        "positions": positions_count,
        "positions_count": positions_count,
        "money_management": money_management,
        "symbols": symbols,
    }


def _position(
    ticket: int = 1001,
    symbol: str = "XAUUSD",
    side: str = "BUY",
    volume: str = "0.01",
    sl: str = "59000.00",
    tp: str = "62000.00",
) -> dict:
    return {
        "ts_server": str(_now()),
        "ticket": str(ticket),
        "symbol": symbol,
        "type": side,
        "side": side,
        "volume": volume,
        "open_price": "60000.00",
        "current_price": "60010.00",
        "profit": "1.23",
        "sl": sl,
        "tp": tp,
        "comment": "FinRobot_XAUUSD_QuickMomentum",
    }


def _deal(
    ticket: int = 2001,
    position_id: int = 9001,
    entry: int = 1,
    ts_server: int | None = None,
    symbol: str = "XAUUSD",
    comment: str = "FinRobot_XAUUSD_QuickMomentum",
) -> dict:
    server_ts = _now() if ts_server is None else ts_server
    return {
        "ts_server": str(server_ts),
        "ticket": str(ticket),
        "order": "9001",
        "position_id": str(position_id),
        "symbol": symbol,
        "entry": str(entry),
        "type": "0",
        "volume": "0.01",
        "price": "60000.00",
        "profit": "0.0",
        "commission": "-0.10",
        "swap": "0.00",
        "comment": comment,
    }


def _ack(
    command_id: int = 3001,
    status: str = "AUTO_FILLED",
    source: str = "AUTO",
    symbol: str = "XAUUSD",
) -> dict:
    server_ts = _now()
    return {
        "ts_server": str(server_ts),
        "command_id": str(command_id),
        "id": str(command_id),
        "status": status,
        "message": "filled",
        "symbol": symbol,
        "side": "BUY",
        "volume": "0.01",
        "price": "60000.00",
        "source": source,
    }
