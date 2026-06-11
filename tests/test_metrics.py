from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for metrics tests")

from finrobot import data_store, metrics  # noqa: E402


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(db)
    data_store.init_schema(connection)
    yield connection
    connection.close()


def test_compute_snapshot_empty_warehouse_has_no_live_status(con):
    snap = metrics.compute_snapshot(con=con, common_dir=None, pm2_restarts=None)

    assert snap.heartbeat_age_seconds is None
    assert snap.open_managed_positions is None
    assert snap.per_symbol == {}
    assert snap.warehouse == {"status": 0, "positions": 0, "deals": 0, "acks": 0}


def test_compute_snapshot_fresh_status_heartbeat(con, tmp_path):
    _write_status(tmp_path, ts=_now())

    snap = metrics.compute_snapshot(con=con, common_dir=tmp_path, pm2_restarts=None)

    assert snap.heartbeat_age_seconds is not None
    assert snap.heartbeat_age_seconds < 5.0
    assert snap.heartbeat_stale is False


def test_compute_snapshot_stale_status_heartbeat(con, tmp_path):
    status_path = _write_status(tmp_path, ts=_now())
    old = time.time() - 120
    os.utime(status_path, (old, old))

    snap = metrics.compute_snapshot(
        con=con,
        common_dir=tmp_path,
        pm2_restarts=None,
        heartbeat_stale_seconds=60,
    )

    assert snap.heartbeat_age_seconds is not None
    assert snap.heartbeat_age_seconds >= 119
    assert snap.heartbeat_stale is True


def test_compute_snapshot_reads_per_symbol_status(con, tmp_path):
    _write_status(
        tmp_path,
        ts=_now(),
        symbols=[
            {
                "symbol": "BTCUSD",
                "last_signal": "no_signal rsi=51.2",
                "auto_positions": 2,
                "session_open": 1,
                "spread_points": 1200.0,
            }
        ],
    )

    snap = metrics.compute_snapshot(con=con, common_dir=tmp_path, pm2_restarts=None)

    assert snap.per_symbol["BTCUSD"]["last_signal"] == "no_signal rsi=51.2"
    assert snap.per_symbol["BTCUSD"]["auto_positions"] == 2
    assert snap.per_symbol["BTCUSD"]["session_open"] is True
    assert snap.per_symbol["BTCUSD"]["spread_points"] == 1200.0


def test_compute_snapshot_clock_skew_with_two_rows_returns_median(con):
    now = _now()
    for index, skew in enumerate((100, 110)):
        data_store.ingest_status(con, _status(ts_server=now + skew + index, ts_local=now + index))

    snap = metrics.compute_snapshot(con=con, common_dir=None, pm2_restarts=None)

    # 2 rows is enough; median of {100, 110} is 105
    assert snap.clock_skew_seconds == 105


def test_compute_snapshot_clock_skew_uses_median(con):
    now = _now()
    for index, skew in enumerate((100, 110, 105)):
        data_store.ingest_status(con, _status(ts_server=now + skew + index, ts_local=now + index))

    snap = metrics.compute_snapshot(
        con=con,
        common_dir=None,
        pm2_restarts=None,
        clock_skew_window_seconds=600,
    )

    assert snap.clock_skew_seconds == 105


def test_compute_snapshot_clock_skew_none_with_zero_rows(con):
    # Empty warehouse: no status rows, clock_skew_seconds should be None
    snap = metrics.compute_snapshot(con=con, common_dir=None, pm2_restarts=None)

    assert snap.clock_skew_seconds is None


def test_snapshot_to_dict_is_json_serializable(con, tmp_path):
    _write_status(tmp_path, ts=_now())
    snap = metrics.compute_snapshot(con=con, common_dir=tmp_path, pm2_restarts=2)

    payload = metrics.snapshot_to_dict(snap)

    assert json.loads(json.dumps(payload))["pm2_mt5_restarts"] == 2


def test_get_pm2_restarts_returns_none_when_pm2_missing_or_process_absent(monkeypatch):
    def missing_pm2(*args, **kwargs):
        raise FileNotFoundError("pm2")

    monkeypatch.setattr(metrics.subprocess, "run", missing_pm2)
    assert metrics.get_pm2_restarts() is None

    class Proc:
        returncode = 0
        stdout = json.dumps([{"name": "other", "pm2_env": {"restart_time": 3}}])

    monkeypatch.setattr(metrics.subprocess, "run", lambda *args, **kwargs: Proc())
    assert metrics.get_pm2_restarts("mt5-terminal") is None


def test_write_snapshot_and_load_snapshot_round_trip(con, tmp_path):
    _write_status(tmp_path, ts=_now())
    snap = metrics.compute_snapshot(con=con, common_dir=tmp_path, pm2_restarts=4)
    out = tmp_path / "nested" / "metrics.json"

    metrics.write_snapshot(snap, out)

    loaded = metrics.load_snapshot(out)
    assert loaded is not None
    assert loaded["pm2_mt5_restarts"] == 4
    assert metrics.load_snapshot(tmp_path / "missing.json") is None


def test_compute_snapshot_propagates_pm2_restarts(con):
    snap = metrics.compute_snapshot(con=con, common_dir=None, pm2_restarts=14)

    assert snap.pm2_mt5_restarts == 14


def test_compute_snapshot_end_to_end_common_files_and_warehouse(con, tmp_path):
    now = _now()
    common = tmp_path / "common"
    common.mkdir()
    _write_status(
        common,
        ts=now + 120,
        positions=1,
        balance=1007048.22,
        equity=1007050.0,
        symbols=[
            {
                "symbol": "XAUUSD",
                "last_signal": "xau_pda_reject ATR_impulse pda=0.37",
                "auto_positions": 1,
                "session_open": 1,
                "spread_points": 5.0,
            }
        ],
    )
    _write_csv(
        common / "finrobot_positions.csv",
        ["time", "ticket", "symbol", "type", "volume", "open_price", "current_price", "profit", "sl", "tp", "comment"],
        [_position(ticket=301, symbol="XAUUSD")],
    )
    _write_csv(
        common / "finrobot_deals.csv",
        ["time", "ticket", "order", "position_id", "symbol", "entry", "type", "volume", "price", "profit", "commission", "swap", "comment"],
        [_deal(ticket=401, position_id=301, symbol="XAUUSD", ts_server=now + 121)],
    )

    data_store.ingest_status(
        con,
        _status(
            ts_server=now + 120,
            ts_local=now,
            positions_count=1,
            balance=1007048.22,
            equity=1007050.0,
            symbols=[{"symbol": "XAUUSD", "last_signal": "ok"}],
        ),
    )
    data_store.ingest_positions(con, [_position(ticket=301, symbol="XAUUSD")], ts_server=now + 120)
    data_store.ingest_deals(
        con,
        [_deal(ticket=401, position_id=301, symbol="XAUUSD", ts_server=now + 121)],
    )

    snap = metrics.compute_snapshot(con=con, common_dir=common, pm2_restarts=1)

    assert snap.warehouse["status"] == 1
    assert snap.warehouse["positions"] == 1
    assert snap.warehouse["deals"] == 1
    assert snap.warehouse_freshness["status"] == 1
    assert snap.open_managed_positions == 1
    assert snap.balance == 1007048.22
    assert snap.per_symbol == {
        "XAUUSD": {
            "last_signal": "xau_pda_reject ATR_impulse pda=0.37",
            "auto_positions": 1,
            "session_open": True,
            "spread_points": 5.0,
        }
    }


def _now() -> int:
    return int(time.time())


def _status(
    ts_server: int,
    ts_local: int | None = None,
    positions_count: int = 0,
    balance: float = 1000.0,
    equity: float = 1000.0,
    symbols: list[dict] | None = None,
) -> dict:
    if symbols is None:
        symbols = [{"symbol": "BTCUSD", "last_signal": "no_signal"}]
    return {
        "ts": ts_server,
        "ts_local": ts_local if ts_local is not None else _now(),
        "login": 123456,
        "server": "ICMarketsSC-Demo",
        "balance": balance,
        "equity": equity,
        "margin": 0.0,
        "free_margin": equity,
        "positions": positions_count,
        "money_management": {
            "loss_limit_reached": 0,
            "risk_lot_sizing": 1,
            "today_closed_pnl": 12.34,
            "daily_risk_per_trade_fraction": 0.001,
            "daily_loss_limit_fraction": 0.01,
        },
        "symbols": symbols,
    }


def _write_status(
    common: Path,
    ts: int,
    positions: int = 0,
    balance: float = 1000.0,
    equity: float = 1000.0,
    symbols: list[dict] | None = None,
) -> Path:
    common.mkdir(parents=True, exist_ok=True)
    path = common / "finrobot_status.json"
    path.write_text(
        json.dumps(
            _status(
                ts_server=ts,
                positions_count=positions,
                balance=balance,
                equity=equity,
                symbols=symbols,
            )
        )
    )
    return path


def _position(ticket: int, symbol: str = "BTCUSD") -> dict:
    return {
        "ts_local": str(_now()),
        "ticket": str(ticket),
        "symbol": symbol,
        "type": "BUY",
        "side": "BUY",
        "volume": "0.01",
        "open_price": "60000.00",
        "current_price": "60010.00",
        "profit": "1.23",
        "sl": "59000.00",
        "tp": "62000.00",
        "comment": f"FinRobot_{symbol}_QuickMomentum",
    }


def _deal(ticket: int, position_id: int, symbol: str = "BTCUSD", ts_server: int | None = None) -> dict:
    return {
        "ts_server": str(ts_server if ts_server is not None else _now()),
        "ts_local": str(_now()),
        "ticket": str(ticket),
        "order": "9001",
        "position_id": str(position_id),
        "symbol": symbol,
        "entry": "1",
        "type": "0",
        "volume": "0.01",
        "price": "60000.00",
        "profit": "3.21",
        "commission": "-0.10",
        "swap": "0.00",
        "comment": f"FinRobot_{symbol}_QuickMomentum",
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
