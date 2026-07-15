"""Tests for scripts/healthcheck.py."""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from healthcheck import (  # noqa: E402
    check_disk_usage,
    check_heartbeat,
    check_loss_limit,
    check_pm2,
    check_research_freshness,
    check_unprotected_positions,
)


def _write_status(common: Path, **mm_overrides) -> None:
    mm = {
        "day": 20260611,
        "daily_equity_snapshot": 1000000.0,
        "today_closed_pnl": -0.0,
        "daily_risk_per_trade_fraction": 0.001,
        "daily_loss_limit_fraction": 0.01,
        "loss_limit_reached": 0,
        "risk_lot_sizing": 1,
        "auto_close_no_sl_tp": 1,
    }
    mm.update(mm_overrides)
    (common / "aiquanttrader_status.json").write_text(
        json.dumps(
            {
                "ts": int(time.time()),
                "balance": 1000000.0,
                "equity": 1000000.0,
                "money_management": mm,
            }
        )
    )


def _write_positions(common: Path, positions: list[dict]) -> None:
    path = common / "aiquanttrader_positions.csv"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "time",
                "ticket",
                "symbol",
                "type",
                "volume",
                "open_price",
                "current_price",
                "profit",
                "sl",
                "tp",
                "comment",
            ],
        )
        writer.writeheader()
        for p in positions:
            writer.writerow(p)


def test_check_heartbeat_missing_file(tmp_path):
    result = check_heartbeat(tmp_path, stale_seconds=60)
    assert not result.ok
    assert "missing" in result.detail.lower()


def test_check_heartbeat_fresh(tmp_path):
    (tmp_path / "aiquanttrader_status.json").write_text("{}")
    result = check_heartbeat(tmp_path, stale_seconds=60)
    assert result.ok
    assert "age" in result.detail


def test_check_heartbeat_stale(tmp_path):
    p = tmp_path / "aiquanttrader_status.json"
    p.write_text("{}")
    import os

    old = time.time() - 120
    os.utime(p, (old, old))
    result = check_heartbeat(tmp_path, stale_seconds=60)
    assert not result.ok
    assert result.extra["age_seconds"] > 60


def test_check_loss_limit_ok(tmp_path):
    status = {"money_management": {"loss_limit_reached": 0, "today_closed_pnl": 0.0}}
    result = check_loss_limit(status)
    assert result.ok
    assert "0" in result.detail


def test_check_loss_limit_breach(tmp_path):
    status = {"money_management": {"loss_limit_reached": 1, "today_closed_pnl": -12345.6}}
    result = check_loss_limit(status)
    assert not result.ok
    assert "blocked" in result.detail


def test_check_loss_limit_missing_block():
    # If money_management is missing, treat as ok (heartbeat check catches it).
    result = check_loss_limit({})
    assert result.ok


def test_check_unprotected_positions_empty(tmp_path):
    _write_positions(tmp_path, [])
    result = check_unprotected_positions(tmp_path, {})
    assert result.ok


def test_check_unprotected_positions_all_protected(tmp_path):
    _write_positions(
        tmp_path,
        [
            {
                "time": "2026-06-11 10:00:00",
                "ticket": "1",
                "symbol": "XAUUSD",
                "type": "BUY",
                "volume": "0.01",
                "open_price": "2000.0",
                "current_price": "2010.0",
                "profit": "10.0",
                "sl": "1990.0",
                "tp": "2020.0",
                "comment": "AIQuantTrader_XAUUSD_MACD_trend",
            },
        ],
    )
    result = check_unprotected_positions(tmp_path, {})
    assert result.ok


def test_check_unprotected_positions_fails_when_auto_close_disabled(tmp_path):
    _write_positions(
        tmp_path,
        [
            {
                "time": "2026-06-11 10:00:00",
                "ticket": "1",
                "symbol": "XAUUSD",
                "type": "BUY",
                "volume": "0.01",
                "open_price": "2000.0",
                "current_price": "2010.0",
                "profit": "10.0",
                "sl": "0.0",
                "tp": "0.0",
                "comment": "rogue",
            },
        ],
    )
    result = check_unprotected_positions(tmp_path, {"money_management": {"auto_close_no_sl_tp": 0}})
    assert not result.ok
    assert "auto_close_no_sl_tp is disabled" in result.detail
    assert result.extra["auto_close_no_sl_tp"] is False


def test_check_unprotected_positions_warns_when_auto_close_enabled(tmp_path):
    _write_positions(
        tmp_path,
        [
            {
                "time": "2026-06-11 10:00:00",
                "ticket": "1",
                "symbol": "XAUUSD",
                "type": "BUY",
                "volume": "0.01",
                "open_price": "2000.0",
                "current_price": "2010.0",
                "profit": "10.0",
                "sl": "0.0",
                "tp": "0.0",
                "comment": "rogue",
            },
        ],
    )
    result = check_unprotected_positions(
        tmp_path, {"money_management": {"auto_close_no_sl_tp": 1}}
    )
    assert result.ok
    assert "auto_close_no_sl_tp is enabled" in result.detail


def test_check_pm2_returns_check_result():
    # We can't fully test pm2 without a real pm2 install; just confirm the
    # function returns a CheckResult and the name reflects the process.
    result = check_pm2("nonexistent-process-for-test")
    # Either ok=False (process not found) or ok=False (pm2 missing). Both fine.
    assert hasattr(result, "ok")
    assert hasattr(result, "name")
    assert hasattr(result, "detail")


def test_check_disk_usage_fails_at_configured_ceiling(tmp_path, monkeypatch):
    usage = type("Usage", (), {"total": 100, "used": 90, "free": 10})()
    monkeypatch.setattr("healthcheck.shutil.disk_usage", lambda path: usage)

    result = check_disk_usage(tmp_path, max_used_percent=85.0)

    assert not result.ok
    assert result.extra["used_percent"] == 90.0


def test_check_research_freshness_allows_pending_first_run(tmp_path):
    result = check_research_freshness(tmp_path / "missing.jsonl", max_age_hours=14)

    assert result.ok
    assert result.name == "research_cycle_pending"


def test_check_research_freshness_accepts_recent_success(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "event": "autonomous_strategy_lab",
                "result": {"enabled": True, "returncode": 0, "timed_out": False},
            }
        )
        + "\n"
    )

    result = check_research_freshness(journal, max_age_hours=14)

    assert result.ok
    assert "succeeded" in result.detail


def test_check_research_freshness_rejects_timeout(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "event": "autonomous_strategy_lab",
                "result": {
                    "enabled": True,
                    "returncode": 124,
                    "timed_out": True,
                    "duration_seconds": 1800,
                },
            }
        )
        + "\n"
    )

    result = check_research_freshness(journal, max_age_hours=14)

    assert not result.ok
    assert "timed out" in result.detail


def test_check_research_freshness_rejects_stale_success(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        json.dumps(
            {
                "ts": time.time() - 15 * 3600,
                "event": "autonomous_strategy_lab",
                "result": {"enabled": True, "returncode": 0},
            }
        )
        + "\n"
    )

    result = check_research_freshness(journal, max_age_hours=14)

    assert not result.ok
    assert "old" in result.detail
