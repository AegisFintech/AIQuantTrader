from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for warehouse tests")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from finrobot import data_store  # noqa: E402
from mt5_ingest_common_files import ingest_common_files  # noqa: E402


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(db)
    data_store.init_schema(connection)
    yield connection
    connection.close()


def test_init_schema_is_idempotent(tmp_path):
    connection = data_store.connect(tmp_path / "warehouse.duckdb")
    try:
        data_store.init_schema(connection)
        data_store.init_schema(connection)
    finally:
        connection.close()


def test_ingest_status_minimal_returns_one_row(con):
    assert data_store.ingest_status(con, {"ts": 1780000000}) == 1
    row = con.execute("SELECT ts_server FROM status").fetchone()
    assert row == (1780000000,)


def test_ingest_status_is_idempotent_on_server_timestamp(con):
    status = {"ts": 1780000001, "balance": "1000.50"}
    assert data_store.ingest_status(con, status) == 1
    assert data_store.ingest_status(con, status) == 0
    assert con.execute("SELECT count(*) FROM status").fetchone()[0] == 1


def test_ingest_positions_is_idempotent_on_ticket_and_timestamp(con):
    rows = [
        _position(ticket=11, symbol="BTCUSD", side="BUY"),
        _position(ticket=12, symbol="XAUUSD", side="SELL"),
    ]
    assert data_store.ingest_positions(con, rows, ts_server=1780000002) == 2
    assert data_store.ingest_positions(con, rows, ts_server=1780000002) == 0
    assert con.execute("SELECT count(*) FROM positions").fetchone()[0] == 2


def test_ingest_deals_entry_in_and_out(con):
    rows = [
        _deal(ticket=101, position_id=5001, entry=0, profit=0.0),
        _deal(ticket=102, position_id=5001, entry=1, profit=12.34),
    ]
    assert data_store.ingest_deals(con, rows) == 2
    entries = con.execute("SELECT entry FROM deals ORDER BY ticket").fetchall()
    assert entries == [(0,), (1,)]


def test_ingest_acks_is_idempotent_on_command_and_timestamp(con):
    rows = [
        _ack(command_id=1, time="2026-06-10 10:00:00", status="AUTO_FILLED"),
        _ack(command_id=2, time="2026-06-10 10:01:00", status="OK"),
        _ack(command_id=3, time="2026-06-10 10:02:00", status="RISK_CLOSED"),
    ]
    assert data_store.ingest_acks(con, rows) == 3
    assert data_store.ingest_acks(con, rows) == 0
    sources = con.execute("SELECT source FROM acks ORDER BY command_id").fetchall()
    assert sources == [("AUTO",), ("COMMAND",), ("AUTO",)]


def test_missing_optional_fields_do_not_crash_and_store_nulls(con):
    assert data_store.ingest_positions(con, [{"ticket": "99"}], ts_server=1780000003) == 1
    row = con.execute(
        "SELECT symbol, profit, sl, tp FROM positions WHERE ticket = 99"
    ).fetchone()
    assert row == (None, None, None, None)


def test_query_summary_counts_rows(con):
    data_store.ingest_status(con, {"ts": 1780000010})
    data_store.ingest_positions(con, [_position(ticket=21)], ts_server=1780000010)
    data_store.ingest_deals(con, [_deal(ticket=201, position_id=7001, entry=1)])
    data_store.ingest_acks(con, [_ack(command_id=31)])
    assert data_store.query_summary(con) == {
        "status": 1,
        "positions": 1,
        "deals": 1,
        "acks": 1,
        "latest_status_ts_server": 1780000010,
    }


def test_ingest_common_files_flow_with_tmp_common_files(tmp_path):
    common = tmp_path / "common"
    common.mkdir()
    warehouse = tmp_path / "warehouse.duckdb"
    (common / "finrobot_status.json").write_text(
        json.dumps(
            {
                "ts": 1780000020,
                "login": "123456",
                "server": "ICMarketsSC-Demo",
                "balance": "1000.00",
                "equity": "1001.00",
                "positions": 1,
                "money_management": {"risk_lot_sizing": 1},
                "symbols": [{"symbol": "BTCUSD"}],
                "ea_version": "1.30",
                "git_sha": "abc123",
            }
        )
    )
    _write_csv(
        common / "finrobot_positions.csv",
        ["time", "ticket", "symbol", "type", "volume", "open_price", "current_price", "profit", "sl", "tp", "comment"],
        [_position(ticket=301, symbol="BTCUSD", side="BUY")],
    )
    _write_csv(
        common / "finrobot_deals.csv",
        ["time", "ticket", "order", "position_id", "symbol", "entry", "type", "volume", "price", "profit", "commission", "swap", "comment"],
        [_deal(ticket=401, position_id=8001, entry=1, profit=3.21)],
    )
    (common / "finrobot_acks.csv").write_text(
        "41,2026.06.10 10:00:00,AUTO_FILLED,BTCUSD strategy QuickMomentum,BTCUSD,BUY,0.01,60000.00\n"
    )

    result = ingest_common_files(common, warehouse)

    assert result["inserted"] == {"status": 1, "positions": 1, "deals": 1, "acks": 1}
    assert result["summary"]["status"] == 1
    connection = data_store.connect(warehouse)
    try:
        assert data_store.query_summary(connection) == {
            "status": 1,
            "positions": 1,
            "deals": 1,
            "acks": 1,
            "latest_status_ts_server": 1780000020,
        }
    finally:
        connection.close()


def _position(ticket: int, symbol: str = "BTCUSD", side: str = "BUY") -> dict:
    return {
        "time": "2026-06-10 10:00:00",
        "ticket": str(ticket),
        "symbol": symbol,
        "type": side,
        "volume": "0.01",
        "open_price": "60000.00",
        "current_price": "60010.00",
        "profit": "1.23",
        "sl": "59000.00",
        "tp": "62000.00",
        "comment": "FinRobot_BTCUSD_QuickMomentum",
    }


def _deal(ticket: int, position_id: int, entry: int, profit: float = 0.0) -> dict:
    return {
        "time": "2026-06-10 10:00:00",
        "ticket": str(ticket),
        "order": "9001",
        "position_id": str(position_id),
        "symbol": "BTCUSD",
        "entry": str(entry),
        "type": "0",
        "volume": "0.01",
        "price": "60000.00",
        "profit": str(profit),
        "commission": "-0.10",
        "swap": "0.00",
        "comment": "FinRobot_BTCUSD_QuickMomentum",
    }


def _ack(
    command_id: int,
    time: str = "2026-06-10 10:00:00",
    status: str = "AUTO_FILLED",
) -> dict:
    return {
        "id": str(command_id),
        "time": time,
        "status": status,
        "message": "filled",
        "symbol": "BTCUSD",
        "side": "BUY",
        "volume": "0.01",
        "price": "60000.00",
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
