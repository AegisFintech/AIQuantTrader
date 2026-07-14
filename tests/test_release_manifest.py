from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="duckdb package is required for warehouse tests")

from finrobot import data_store, release_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_build_manifest_returns_required_keys():
    manifest = release_manifest.build_manifest(ROOT)

    assert {
        "schema_version",
        "generated_at",
        "git_sha",
        "git_short",
        "git_dirty",
        "ea_version",
        "ea_source_path",
        "config_inputs",
        "managed_symbols",
        "python_version",
    } <= set(manifest)


def test_build_manifest_extracts_ea_version_from_mq5_source():
    manifest = release_manifest.build_manifest(ROOT)

    assert manifest["ea_version"] == "1.39"


def test_build_manifest_extracts_auto_symbols_as_managed_symbols():
    manifest = release_manifest.build_manifest(ROOT)

    assert manifest["config_inputs"]["AutoSymbols"] == "XAUUSD"
    assert manifest["managed_symbols"] == ["XAUUSD"]


def test_build_manifest_extracts_compounding_risk_defaults():
    manifest = release_manifest.build_manifest(ROOT)

    assert manifest["config_inputs"]["MaxLotPerTrade"] == "50.0"
    assert manifest["config_inputs"]["MaxLotPerTradeXAUUSD"] == "50.0"
    assert manifest["config_inputs"]["MinSmcConfluenceScoreXAUUSD"] == "4"
    assert manifest["config_inputs"]["DailyRiskPerTradeFraction"] == "0.0100"
    assert manifest["config_inputs"]["DailyLossLimitFraction"] == "0.01"


def test_build_manifest_returns_empty_git_fields_outside_git_repo(tmp_path):
    _write_ea(tmp_path, version="9.99")

    manifest = release_manifest.build_manifest(tmp_path)

    assert manifest["git_sha"] is None
    assert manifest["git_short"] is None
    assert manifest["git_dirty"] is False
    assert manifest["ea_version"] == "9.99"


@pytest.mark.real_repo
def test_build_manifest_populates_git_sha_inside_real_finrobot_repo():
    if not (ROOT / ".git").exists() or not (ROOT / "broker" / "mt5" / "FinRobotBridgeEA.mq5").exists():
        pytest.skip("not running inside the FinRobot git repo")

    manifest = release_manifest.build_manifest(ROOT)

    assert re.fullmatch(r"[0-9a-f]{40}", manifest["git_sha"] or "")


def test_write_manifest_creates_json_file_at_default_path(tmp_path):
    _write_ea(tmp_path)

    path = release_manifest.write_manifest(tmp_path)

    assert path == tmp_path / "state" / "mt5" / "RELEASE.json"
    manifest = json.loads(path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["ea_version"] == "1.30"


def test_write_ea_manifest_creates_key_value_file(tmp_path):
    _write_ea(tmp_path)

    path = release_manifest.write_ea_manifest(tmp_path)

    assert path == tmp_path / "state" / "mt5" / "EA_MANIFEST.txt"
    lines = path.read_text().splitlines()
    assert "schema_version=1" in lines
    assert "ea_version=1.30" in lines
    assert any(line.startswith("git_sha=") for line in lines)
    assert any(line.startswith("generated_at=") for line in lines)
    assert "git_dirty=0" in lines


def test_data_store_release_defaults_returns_manifest_tuple():
    assert data_store.release_defaults({"ea_version": "1.30", "git_sha": "abc123"}) == (
        "1.30",
        "abc123",
    )


def test_ingest_status_without_explicit_version_reads_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "ROOT", tmp_path)
    _write_release_json(tmp_path, ea_version="1.30", git_sha="abc123")
    con = data_store.connect(tmp_path / "warehouse.duckdb")
    try:
        data_store.init_schema(con)

        assert data_store.ingest_status(con, {"ts": 1780000100}) == 1

        row = con.execute("SELECT ea_version, git_sha FROM status").fetchone()
        assert row == ("1.30", "abc123")
    finally:
        con.close()


def test_ingest_status_prefers_live_status_over_manifest(tmp_path, monkeypatch):
    """Live finrobot_status.json must override state/mt5/RELEASE.json.

    The deployed .ex5 carries the compile-time SHA; state/mt5/RELEASE.json
    carries the current HEAD SHA. They diverge between deploys. The script
    must surface the actually-deployed SHA (live status), not the HEAD
    snapshot (manifest), so downstream consumers see the real version.
    """
    from scripts import mt5_ingest_common_files

    monkeypatch.setattr(data_store, "ROOT", tmp_path)
    _write_release_json(tmp_path, ea_version="1.30", git_sha="head-sha-current")

    common = tmp_path / "common"
    common.mkdir()
    (common / "finrobot_status.json").write_text(
        json.dumps(
            {
                "ts": 1780000100,
                "login": 123456,
                "server": "ICMarketsSC-Demo",
                "balance": 1000.0,
                "equity": 1000.0,
                "margin": 0.0,
                "free_margin": 1000.0,
                "positions": 0,
                "money_management": {
                    "loss_limit_reached": 0,
                    "risk_lot_sizing": 1,
                    "today_closed_pnl": 0.0,
                    "daily_risk_per_trade_fraction": 0.001,
                    "daily_loss_limit_fraction": 0.01,
                },
                "symbols": [],
                "ea_version": "1.31",
                "git_sha": "deployed-sha-older",
            }
        )
    )
    (common / "finrobot_positions.csv").write_text(
        "time,ticket,symbol,type,side,volume,open_price,current_price,profit,sl,tp,comment\n"
    )
    (common / "finrobot_deals.csv").write_text(
        "time,ticket,order,position_id,symbol,entry,type,volume,price,profit,commission,swap,comment\n"
    )
    (common / "finrobot_acks.csv").write_text(
        "time,ticket,order,position_id,symbol,action,side,volume,price,sl,tp,comment,source\n"
    )

    warehouse = tmp_path / "warehouse.duckdb"
    result = mt5_ingest_common_files.ingest_common_files(common, warehouse)

    assert result["inserted"]["status"] == 1

    con = data_store.connect(warehouse)
    try:
        row = con.execute("SELECT ea_version, git_sha FROM status").fetchone()
    finally:
        con.close()
    assert row == ("1.31", "deployed-sha-older")


def _write_ea(root: Path, version: str = "1.30") -> Path:
    mq5 = root / "broker" / "mt5" / "FinRobotBridgeEA.mq5"
    mq5.parent.mkdir(parents=True, exist_ok=True)
    mq5.write_text(
        "\n".join(
            [
                "#property strict",
                f'#property version "{version}"',
                'input string AutoSymbols = "XAUUSD";',
                "input bool UseDailyRiskLotSizing = true;",
            ]
        )
        + "\n"
    )
    return mq5


def _write_release_json(root: Path, ea_version: str, git_sha: str) -> Path:
    path = root / "state" / "mt5" / "RELEASE.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ea_version": ea_version, "git_sha": git_sha}))
    return path
