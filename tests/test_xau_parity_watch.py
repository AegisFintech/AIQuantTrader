from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "xau_parity_watch.sh"


def test_xau_parity_watch_runs_against_tmp_duckdb(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    common_dir = tmp_path / "common"
    report_dir = tmp_path / "reports"
    common_dir.mkdir()
    _init_prices(warehouse)
    _write_acks(common_dir, [])

    completed = _run_watch(
        tmp_path,
        warehouse=warehouse,
        common_dir=common_dir,
        report_dir=report_dir,
        pytest_status="XFAIL",
    )

    assert completed.returncode == 0, completed.stderr
    report = _single_report(report_dir)
    assert report["live_test_status"] == "XFAIL"
    assert report["n_bars_in_window"] == 0
    assert report["n_acks_in_window"] == 0
    assert report["overlap_count"] == 0


def test_xau_parity_watch_handles_xpass_state(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    common_dir = tmp_path / "common"
    report_dir = tmp_path / "reports"
    common_dir.mkdir()
    _init_prices(warehouse)
    _insert_bar(warehouse, "2026-06-11 00:01:00")
    _write_acks(
        common_dir,
        [
            "1,2026-06-11 00:01:00,AUTO_FILLED,fixture,XAUUSD,BUY,0.01,2300.0",
        ],
    )

    completed = _run_watch(
        tmp_path,
        warehouse=warehouse,
        common_dir=common_dir,
        report_dir=report_dir,
        pytest_status="XPASS",
    )

    assert completed.returncode == 0, completed.stderr
    report = _single_report(report_dir)
    assert report["live_test_status"] == "XPASS"
    assert report["match_rate"] == pytest.approx(1.0)
    assert report["n_bars_in_window"] == 1
    assert report["n_acks_in_window"] == 1
    assert report["overlap_count"] == 1


def test_xau_parity_watch_source_validation(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--data-source",
            "bogus",
            "--report-dir",
            str(tmp_path / "reports"),
            "--registry",
            str(tmp_path / "warehouse.duckdb"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "--data-source must be one of" in completed.stderr


def test_xau_parity_watch_writes_report_file(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    common_dir = tmp_path / "common"
    report_dir = tmp_path / "reports"
    common_dir.mkdir()
    _init_prices(warehouse)
    _write_acks(common_dir, [])

    completed = _run_watch(
        tmp_path,
        warehouse=warehouse,
        common_dir=common_dir,
        report_dir=report_dir,
        pytest_status="XFAIL",
    )

    assert completed.returncode == 0, completed.stderr
    assert len(list(report_dir.glob("xau_parity_*.json"))) == 1
    assert len(list(report_dir.glob("xau_parity_*.log"))) == 1


def test_xau_parity_watch_handles_missing_warehouse(tmp_path: Path) -> None:
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    env = _watch_env(
        tmp_path,
        common_dir=common_dir,
        pytest_status="XFAIL",
    )

    completed = subprocess.run(
        [
            str(SCRIPT),
            "--registry",
            str(tmp_path / "missing.duckdb"),
            "--report-dir",
            str(tmp_path / "reports"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "DuckDB warehouse not found" in completed.stderr


def _run_watch(
    tmp_path: Path,
    *,
    warehouse: Path,
    common_dir: Path,
    report_dir: Path,
    pytest_status: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(SCRIPT),
            "--registry",
            str(warehouse),
            "--report-dir",
            str(report_dir),
            "--from-date",
            "2026-06-11",
            "--to-date",
            "2026-06-11",
        ],
        env=_watch_env(tmp_path, common_dir=common_dir, pytest_status=pytest_status),
        capture_output=True,
        text=True,
        check=False,
    )


def _watch_env(
    tmp_path: Path,
    *,
    common_dir: Path,
    pytest_status: str,
) -> dict[str, str]:
    target = tmp_path / f"test_probe_{pytest_status.lower()}.py"
    _write_probe_test(target)
    env = os.environ.copy()
    env.update(
        {
            "FINROBOT_COMMON_DIR": str(common_dir),
            "FINROBOT_XAU_PARITY_PYTEST_TARGET": str(target),
            "FINROBOT_XAU_PARITY_FIXTURE_STATUS": pytest_status,
            "PYTHON": sys.executable,
        }
    )
    return env


def _write_probe_test(path: Path) -> None:
    path.write_text(
        """\
import os

import pytest


@pytest.mark.xfail(reason="watch fixture")
def test_xau_parity_live_probe():
    if os.environ["FINROBOT_XAU_PARITY_FIXTURE_STATUS"] == "XPASS":
        print("live XAU parity: 1/1 matched (100.00%)")
        assert True
        return
    pytest.xfail("Live June 11+ non-null XAU OHLC bars are not yet exported")
"""
    )


def _init_prices(warehouse: Path) -> None:
    con = duckdb.connect(str(warehouse))
    try:
        con.execute(
            """
            CREATE TABLE prices (
              symbol TEXT NOT NULL,
              ts_server INTEGER NOT NULL,
              ts_local INTEGER NOT NULL,
              open DOUBLE,
              high DOUBLE,
              low DOUBLE,
              close DOUBLE,
              volume DOUBLE,
              spread_price DOUBLE,
              spread_points DOUBLE,
              source TEXT NOT NULL,
              ea_version TEXT,
              git_sha TEXT
            )
            """
        )
    finally:
        con.close()


def _insert_bar(warehouse: Path, timestamp: str) -> None:
    epoch = int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    con = duckdb.connect(str(warehouse))
    try:
        con.execute(
            """
            INSERT INTO prices (
              symbol, ts_server, ts_local, open, high, low, close, volume,
              spread_price, spread_points, source, ea_version, git_sha
            )
            VALUES ('XAUUSD', ?, ?, 2300.0, 2301.0, 2299.0, 2300.5, 1.0, NULL, NULL, 'fixture', NULL, NULL)
            """,
            [epoch, epoch],
        )
    finally:
        con.close()


def _write_acks(common_dir: Path, rows: list[str]) -> None:
    header = "id,time,status,message,symbol,side,volume,price"
    (common_dir / "finrobot_acks.csv").write_text("\n".join([header, *rows]) + "\n")


def _single_report(report_dir: Path) -> dict:
    reports = list(report_dir.glob("xau_parity_*.json"))
    assert len(reports) == 1
    return json.loads(reports[0].read_text())
