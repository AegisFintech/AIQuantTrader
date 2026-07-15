from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for warehouse tests")

ROOT = Path(__file__).resolve().parents[1]

from aiquanttrader import data_store, prices  # noqa: E402
from aiquanttrader.validators import (  # noqa: E402
    Severity,
    reconcile_prices_against_positions,
    validate_price,
)


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "warehouse.duckdb"
    connection = data_store.connect(db)
    data_store.init_schema(connection)
    prices.init_prices_schema(connection)
    yield connection
    connection.close()


def test_load_tsv_bars_reads_existing_xauusd_file_count():
    path = ROOT / "data" / "XAUUSD1.csv"
    expected = sum(1 for line in path.read_text().splitlines() if line.strip())

    rows = list(prices.load_tsv_bars(path))

    assert len(rows) == expected
    assert rows[0]["time"] == "2026-01-19 18:51"
    assert rows[0]["open"] == 4676.055


def test_load_tsv_bars_auto_detects_ohlcv_vs_ohlcv_bidask(tmp_path):
    ohlcv = tmp_path / "XAUUSD1.csv"
    bidask = tmp_path / "XAUUSD_M1.csv"
    ohlcv.write_text("2026-01-01 00:00\t1\t2\t0.5\t1.5\t10\n")
    bidask.write_text("2026-01-01 00:00\t1\t2\t0.5\t1.5\t10\t1.49\t1.51\n")

    ohlcv_row = next(prices.load_tsv_bars(ohlcv))
    bidask_row = next(prices.load_tsv_bars(bidask))

    assert "bid" not in ohlcv_row
    assert bidask_row["bid"] == 1.49
    assert bidask_row["ask"] == 1.51


def test_load_tsv_bars_skips_blank_lines(tmp_path):
    path = tmp_path / "XAUUSD1.csv"
    path.write_text("\n2026-01-01 00:00\t1\t2\t0.5\t1.5\t10\n\n")

    rows = list(prices.load_tsv_bars(path))

    assert len(rows) == 1


def test_load_tsv_bars_strips_whitespace_and_parses_floats(tmp_path):
    path = tmp_path / "XAUUSD1.csv"
    path.write_text(" 2026-01-01 00:00 \t 1.1 \t 2.2 \t 0.9 \t 1.8 \t 3 \n")

    row = next(prices.load_tsv_bars(path))

    assert row["time"] == "2026-01-01 00:00"
    assert row["open"] == pytest.approx(1.1)
    assert row["volume"] == pytest.approx(3.0)


def test_load_status_bidask_returns_empty_on_missing_file(tmp_path):
    assert prices.load_status_bidask(tmp_path) == []


def test_load_status_bidask_returns_one_snapshot_per_symbol(tmp_path):
    (tmp_path / "aiquanttrader_status.json").write_text(
        json.dumps(
            {
                "ts": 1780000000,
                "symbols": [
                    {"symbol": "XAUUSD", "bid": "4078.01", "ask": "4078.13", "spread_points": "12"},
                    {"symbol": "XAUUSD", "bid": 62545.73, "ask": 62557.73, "spread_points": 1200},
                ],
            }
        )
    )

    snapshots = prices.load_status_bidask(tmp_path)

    assert snapshots == [
        {"symbol": "XAUUSD", "ts": 1780000000, "bid": 4078.01, "ask": 4078.13, "spread_points": 12.0},
        {"symbol": "XAUUSD", "ts": 1780000000, "bid": 62545.73, "ask": 62557.73, "spread_points": 1200.0},
    ]


def test_generate_synthetic_bars_is_deterministic_with_seed():
    first = prices.generate_synthetic_bars("XAUUSD", 5, seed=123)
    second = prices.generate_synthetic_bars("XAUUSD", 5, seed=123)

    assert first == second


def test_generate_synthetic_bars_produces_n_rows():
    assert len(prices.generate_synthetic_bars("XAUUSD", 7)) == 7


def test_generate_synthetic_bars_high_is_not_below_low():
    rows = prices.generate_synthetic_bars("XAUUSD", 20)

    assert all(row["high"] >= row["low"] for row in rows)


def test_init_prices_schema_is_idempotent(con):
    prices.init_prices_schema(con)
    prices.init_prices_schema(con)

    assert con.execute("SELECT count(*) FROM prices").fetchone()[0] == 0


def test_ingest_bars_with_100_bars_returns_100(con):
    bars = prices.generate_synthetic_bars("XAUUSD", 100)

    assert prices.ingest_bars(con, "XAUUSD", bars) == 100


def test_ingest_bars_is_idempotent(con):
    bars = prices.generate_synthetic_bars("XAUUSD", 10)

    assert prices.ingest_bars(con, "XAUUSD", bars) == 10
    assert prices.ingest_bars(con, "XAUUSD", bars) == 0


@pytest.mark.parametrize(
    ("broker_time", "expected_utc"),
    [
        ("2026-01-19 18:51", datetime(2026, 1, 19, 18, 51, tzinfo=timezone.utc)),
        ("2026-07-14 12:20", datetime(2026, 7, 14, 12, 20, tzinfo=timezone.utc)),
    ],
)
def test_ingest_bars_preserves_broker_wall_time(con, broker_time, expected_utc):
    bar = _bar(broker_time)
    bar["time_zone"] = "UTC"

    assert prices.ingest_bars(con, "XAUUSD", [bar]) == 1
    stored = con.execute("SELECT ts_server FROM prices").fetchone()[0]

    assert stored == int(expected_utc.timestamp())


def test_ingest_bidask_snapshots_with_two_snapshots_returns_two(con):
    snapshots = [
        {"symbol": "XAUUSD", "ts": 1780000100, "bid": 4078.01, "ask": 4078.13},
        {"symbol": "GOLD", "ts": 1780000100, "bid": 62545.73, "ask": 62557.73},
    ]

    assert prices.ingest_bidask_snapshots(con, snapshots) == 2
    spreads = con.execute(
        "SELECT symbol, spread_price FROM prices ORDER BY symbol"
    ).fetchall()
    assert spreads == [("GOLD", 12.0), ("XAUUSD", pytest.approx(0.12))]


def test_ingest_bidask_snapshots_is_idempotent_on_symbol_ts_source(con):
    snapshots = [{"symbol": "XAUUSD", "ts": 1780000101, "bid": 4078.01, "ask": 4078.13}]

    assert prices.ingest_bidask_snapshots(con, snapshots) == 1
    assert prices.ingest_bidask_snapshots(con, snapshots) == 0


def test_different_sources_can_write_same_symbol_and_timestamp(con):
    ts_server = 1780000200
    tsv_bar = _bar(ts_server, source="tsv:/tmp/XAUUSD1.csv")
    synthetic_bar = _bar(ts_server, source="synthetic")

    assert prices.ingest_bars(con, "XAUUSD", [tsv_bar]) == 1
    assert prices.ingest_bars(con, "XAUUSD", [synthetic_bar]) == 1
    assert con.execute("SELECT count(*) FROM prices").fetchone()[0] == 2


def test_validate_price_happy_path_empty():
    assert validate_price(_price_row()) == []


def test_validate_price_high_below_low_warns():
    row = _price_row(high=99.0, low=100.0)

    issues = validate_price(row)

    assert _has_warning(issues, "price_ohlc_inconsistent")


def test_validate_price_negative_spread_price_warns():
    row = _price_row(spread_price=-0.1)

    issues = validate_price(row)

    assert _has_warning(issues, "price_spread_price_negative")


def test_reconcile_prices_against_positions_flags_mismatched_open_price(con):
    position_ts = int(time.time())
    data_store.ingest_positions(
        con,
        [
            {
                "ticket": "1001",
                "symbol": "XAUUSD",
                "type": "BUY",
                "volume": "0.01",
                "open_price": "101.0",
                "current_price": "101.0",
                "sl": "99.0",
                "tp": "103.0",
            }
        ],
        ts_server=position_ts,
    )
    prices.ingest_bars(con, "XAUUSD", [_bar(position_ts - 60, close=100.0)])

    issues = reconcile_prices_against_positions(con)

    assert _has_warning(issues, "position_price_mismatch")


def test_end_to_end_synthetic_ingest_query_confirms_rows(con):
    bars = prices.generate_synthetic_bars("XAUUSD", 50, seed=456)

    prices.ingest_bars(con, "XAUUSD", bars)
    count, min_source, max_source = con.execute(
        "SELECT count(*), min(source), max(source) FROM prices WHERE symbol = 'XAUUSD'"
    ).fetchone()

    assert count == 50
    assert min_source == "synthetic"
    assert max_source == "synthetic"


def test_cli_load_historical_prices_dry_run_exits_zero():
    result = subprocess.run(
        [sys.executable, "scripts/load_historical_prices.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[OK] data/XAUUSD1.csv" in result.stdout


def test_cli_load_historical_prices_inserts_then_second_run_inserts_zero(tmp_path):
    env = os.environ.copy()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "XAUUSD1.csv").write_text(
        "2026-01-01 00:00\t1\t2\t0.5\t1.5\t10\n"
        "2026-01-01 00:01\t1.5\t2\t1.4\t1.8\t11\n"
    )
    env["AIQUANTTRADER_DATA_DIR"] = str(data_dir)
    env["AIQUANTTRADER_WAREHOUSE"] = str(tmp_path / "prices.duckdb")

    first = subprocess.run(
        [sys.executable, "scripts/load_historical_prices.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, "scripts/load_historical_prices.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert "[OK]" in first.stdout
    assert "parsed=2 inserted=2 skipped=0" in first.stdout
    assert "inserted=0" in second.stdout


def test_cli_mt5_snapshot_prices_dry_run_exits_zero():
    result = subprocess.run(
        [sys.executable, "scripts/mt5_snapshot_prices.py", "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("[OK] snapshot:")


def _bar(ts_server: int, close: float = 100.0, source: str = "synthetic") -> dict:
    return {
        "time": ts_server,
        "open": 100.0,
        "high": max(101.0, close),
        "low": min(99.0, close),
        "close": close,
        "volume": 1.0,
        "source": source,
    }


def _price_row(**overrides) -> dict:
    row = {
        "symbol": "XAUUSD",
        "ts_server": int(time.time()),
        "ts_local": int(time.time()),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1.0,
        "spread_price": 0.12,
        "spread_points": None,
        "source": "tsv:/tmp/XAUUSD1.csv",
    }
    row.update(overrides)
    return row


def _has_warning(issues, check: str) -> bool:
    return any(issue.severity == Severity.WARNING and issue.check == check for issue in issues)
