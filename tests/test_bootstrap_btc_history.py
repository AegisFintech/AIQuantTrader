from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for warehouse tests")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from finrobot import data_store, prices  # noqa: E402
import bootstrap_btc_history as bootstrap  # noqa: E402


def test_bootstrap_synthetic_inserts_into_duckdb(tmp_path, capsys):
    warehouse = tmp_path / "warehouse.duckdb"

    rc = bootstrap.main(
        [
            "--source",
            "synthetic",
            "--n-bars",
            "1000",
            "--base-price",
            "60000",
            "--warehouse",
            str(warehouse),
        ]
    )

    assert rc == 0
    assert "inserted=1000" in capsys.readouterr().out
    con = data_store.connect(warehouse)
    try:
        count = con.execute(
            "SELECT COUNT(*) FROM prices WHERE symbol = 'BTCUSD' AND source = 'synthetic'"
        ).fetchone()[0]
    finally:
        con.close()
    assert count >= 1000


def test_bootstrap_synthetic_deterministic(tmp_path):
    first_db = tmp_path / "first.duckdb"
    second_db = tmp_path / "second.duckdb"
    args = [
        "--source",
        "synthetic",
        "--n-bars",
        "1000",
        "--base-price",
        "60000",
        "--seed",
        "123",
    ]

    assert bootstrap.main([*args, "--warehouse", str(first_db)]) == 0
    assert bootstrap.main([*args, "--warehouse", str(second_db)]) == 0

    first_count, first_close = _synthetic_count_and_first_close(first_db)
    second_count, second_close = _synthetic_count_and_first_close(second_db)
    assert first_count == second_count == 1000
    assert first_close == second_close


def test_bootstrap_synthetic_dry_run(tmp_path, capsys):
    warehouse = tmp_path / "dry-run.duckdb"

    rc = bootstrap.main(
        [
            "--source",
            "synthetic",
            "--n-bars",
            "1000",
            "--warehouse",
            str(warehouse),
            "--dry-run",
        ]
    )

    assert rc == 0
    assert "dry_run=1" in capsys.readouterr().out
    con = data_store.connect(warehouse)
    try:
        prices.init_prices_schema(con)
        count = con.execute(
            "SELECT COUNT(*) FROM prices WHERE symbol = 'BTCUSD' AND source = 'synthetic'"
        ).fetchone()[0]
    finally:
        con.close()
    assert count == 0


def test_bootstrap_mt5_export_runs_harvester(tmp_path, capsys):
    common_dir = tmp_path / "common"
    common_dir.mkdir()

    rc = bootstrap.main(
        [
            "--source",
            "mt5-export",
            "--common-dir",
            str(common_dir),
            "--data-dir",
            str(tmp_path / "data"),
            "--warehouse",
            str(tmp_path / "warehouse.duckdb"),
        ]
    )

    assert rc == 0
    stdout = capsys.readouterr().out
    assert "no MT5 M1 export files" in stdout
    assert "inserted=0" in stdout


def test_bootstrap_third_party_prints_todo(capsys):
    rc = bootstrap.main(["--source", "third-party"])

    assert rc == 0
    assert "TODO: third-party source not yet implemented" in capsys.readouterr().out


def test_bootstrap_source_validation():
    result = subprocess.run(
        [sys.executable, "scripts/bootstrap_btc_history.py", "--source", "bogus"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def _synthetic_count_and_first_close(warehouse: Path) -> tuple[int, float]:
    con = data_store.connect(warehouse)
    try:
        row = con.execute(
            """
            SELECT COUNT(*), first(close ORDER BY ts_server)
            FROM prices
            WHERE symbol = 'BTCUSD' AND source = 'synthetic'
            """
        ).fetchone()
    finally:
        con.close()
    return int(row[0]), float(row[1])
