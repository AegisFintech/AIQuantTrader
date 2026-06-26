from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from finrobot.prices import load_tsv_bars
import harvest_mt5_export as harvest


ROOT = Path(__file__).resolve().parents[1]


def test_parse_export_filename_xauusd():
    assert harvest.parse_export_filename("finrobot_export_XAUUSD_M1.tsv") == ("XAUUSD",)


def test_parse_export_filename_xauusd():
    assert harvest.parse_export_filename("finrobot_export_XAUUSD_M1.tsv") == ("XAUUSD",)


def test_parse_export_filename_rejects_garbage():
    with pytest.raises(harvest.ExportFilenameError):
        harvest.parse_export_filename("garbage.tsv")


def test_copy_to_data_dir_writes_symbol_m1_csv(tmp_path):
    src = _write_export(tmp_path, "XAUUSD", rows=_rows("2026-06-10 12:00", "2026-06-10 12:01"))
    data_dir = tmp_path / "data"

    dest = harvest.copy_to_data_dir(src, data_dir, "XAUUSD")

    assert dest == data_dir / "XAUUSD_M1.csv"
    assert dest.read_text() == src.read_text()


def test_discover_exports_returns_symbol_path_pairs(tmp_path):
    xau = _write_export(tmp_path, "XAUUSD")
    gold = _write_export(tmp_path, "GOLD")
    (tmp_path / "garbage.tsv").write_text("ignored\n")

    assert harvest.discover_exports(tmp_path) == [
        ("GOLD", gold),
        ("XAUUSD", xau),
    ]


def test_discover_exports_missing_common_dir_returns_empty(tmp_path):
    assert harvest.discover_exports(tmp_path / "missing") == []


def test_harvest_all_dry_run_is_repeatable_without_copying(tmp_path):
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    _write_export(common_dir, "XAUUSD", rows=_rows("2026-06-10 12:00", "2026-06-10 12:01"))
    data_dir = tmp_path / "data"

    first = harvest.harvest_all(common_dir, data_dir, warehouse_path=tmp_path / "warehouse.duckdb", dry_run=True)
    second = harvest.harvest_all(common_dir, data_dir, warehouse_path=tmp_path / "warehouse.duckdb", dry_run=True)

    assert [(result.symbol, result.bars, result.inserted, result.dry_run) for result in first] == [
        ("XAUUSD", 2, 0, True)
    ]
    assert [(result.symbol, result.bars, result.inserted, result.dry_run) for result in second] == [
        ("XAUUSD", 2, 0, True)
    ]
    assert not (data_dir / "XAUUSD_M1.csv").exists()


def test_harvest_all_loads_export_into_tmp_warehouse(tmp_path):
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    _write_export(common_dir, "XAUUSD", rows=_rows("2026-06-10 12:00", "2026-06-10 12:01"))
    data_dir = tmp_path / "data"
    warehouse = tmp_path / "warehouse.duckdb"

    results = harvest.harvest_all(common_dir, data_dir, warehouse_path=warehouse)

    assert [(result.symbol, result.bars, result.inserted) for result in results] == [("XAUUSD", 2, 2)]
    assert (data_dir / "XAUUSD_M1.csv").exists()
    con = duckdb.connect(str(warehouse))
    try:
        assert con.execute("SELECT symbol, count(*) FROM prices GROUP BY symbol").fetchall() == [("XAUUSD", 2)]
    finally:
        con.close()


def test_harvest_all_is_idempotent_on_second_load(tmp_path):
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    _write_export(common_dir, "XAUUSD", rows=_rows("2026-06-10 12:00", "2026-06-10 12:01"))
    data_dir = tmp_path / "data"
    warehouse = tmp_path / "warehouse.duckdb"

    first = harvest.harvest_all(common_dir, data_dir, warehouse_path=warehouse)
    second = harvest.harvest_all(common_dir, data_dir, warehouse_path=warehouse)

    assert first[0].inserted == 2
    assert second[0].inserted == 0
    con = duckdb.connect(str(warehouse))
    try:
        assert con.execute("SELECT count(*) FROM prices").fetchone()[0] == 2
    finally:
        con.close()


def test_export_tsv_format_matches_existing_xauusd_layout(tmp_path):
    export_path = _write_export(tmp_path, "XAUUSD", rows=_rows("2026-06-10 12:34"))
    raw_line = export_path.read_text().splitlines()[0]
    fields = raw_line.split("\t")

    assert len(fields) == 6
    assert fields[0] != "time"
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", fields[0])

    exported = next(load_tsv_bars(export_path))
    existing = next(load_tsv_bars(ROOT / "data" / "XAUUSD1.csv"))
    expected_keys = ["time", "open", "high", "low", "close", "volume"]

    assert [key for key in expected_keys if key in exported] == expected_keys
    assert [key for key in expected_keys if key in existing] == expected_keys


def test_harvest_all_filters_symbols_like_cli_option(tmp_path):
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    _write_export(common_dir, "XAUUSD")
    _write_export(common_dir, "XAUUSD")

    results = harvest.harvest_all(
        common_dir,
        tmp_path / "data",
        dry_run=True,
        symbols=harvest.parse_symbol_filter("XAUUSD"),
    )

    assert [result.symbol for result in results] == ["XAUUSD"]


def test_harvest_all_skips_empty_and_unparseable_exports(tmp_path):
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    (common_dir / "finrobot_export_XAUUSD_M1.tsv").write_text("")
    (common_dir / "finrobot_export_XAUUSD_M1.tsv").write_text("time\topen\thigh\tlow\tclose\tvolume\n")

    results = harvest.harvest_all(common_dir, tmp_path / "data", dry_run=True)

    assert results == []


def _write_export(tmp_path: Path, symbol: str, rows: list[str] | None = None) -> Path:
    path = tmp_path / f"finrobot_export_{symbol}_M1.tsv"
    path.write_text("\n".join(rows or _rows("2026-06-10 12:00")) + "\n")
    return path


def _rows(*times: str) -> list[str]:
    return [f"{time}\t1.10000\t1.20000\t1.00000\t1.15000\t42" for time in times]
