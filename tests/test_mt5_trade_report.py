"""Tests for scripts/mt5_trade_report.py helpers."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts/ is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mt5_trade_report import (  # noqa: E402
    RETIRED_AUTO_STRATEGIES,
    money,
    read_csv,
    read_json,
    retired_strategy_fills,
    summarize_deals,
)


def _deal(symbol, position_id, entry, profit, comment="", time="2026-06-10 10:00:00", deal_type=1):
    return {
        "time": time,
        "ticket": str(position_id),
        "order": "1",
        "position_id": str(position_id),
        "symbol": symbol,
        "entry": str(entry),
        "type": str(deal_type),
        "volume": "0.01",
        "price": "100.00",
        "profit": str(profit),
        "commission": "0.0",
        "swap": "0.0",
        "comment": comment,
    }


def test_money_handles_strings_none_and_garbage():
    assert money("12.5") == 12.5
    assert money(None) == 0.0
    assert money("not a number") == 0.0
    assert money(0) == 0.0


def test_read_json_missing_file_returns_empty_dict(tmp_path):
    assert read_json(tmp_path / "nope.json") == {}


def test_read_json_invalid_returns_empty_dict(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_json(bad) == {}


def test_read_csv_missing_or_empty(tmp_path):
    assert read_csv(tmp_path / "missing.csv") == []
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert read_csv(empty) == []


def test_summarize_deals_pairs_entries_with_exits():
    # ENTRY_IN (entry=0) opens a position, profit=0; ENTRY_OUT (entry=1) closes it with realized PnL.
    rows = [
        _deal("XAUUSD", 1, entry=0, profit=0.0, comment="FinRobot_XAUUSD_MACD_trend", time="2026-06-10 10:00:00"),
        _deal("XAUUSD", 1, entry=1, profit=10.0, time="2026-06-10 11:00:00"),
        _deal("XAUUSD", 2, entry=0, profit=0.0, comment="FinRobot_XAUUSD_QuickMomentum_EMA_cross", time="2026-06-10 12:00:00"),
        _deal("XAUUSD", 2, entry=1, profit=-5.0, time="2026-06-10 13:00:00"),
    ]
    summary = summarize_deals(rows)
    assert summary["closed_deals"] == 2
    assert summary["total_pnl"] == 5.0
    by_sym = summary["by_symbol"]
    assert by_sym["XAUUSD"]["n"] == 2
    assert by_sym["XAUUSD"]["pnl"] == 5.0
    assert by_sym["XAUUSD"]["win_rate"] == 0.5
    by_strat = summary["by_strategy"]
    # The report keys strategies by the full entry comment (e.g. "FinRobot_XAUUSD_MACD_trend").
    assert "XAUUSD:FinRobot_XAUUSD_MACD_trend" in by_strat
    assert "XAUUSD:FinRobot_XAUUSD_QuickMomentum_EMA_cross" in by_strat
    by_day = summary["by_day"]
    assert by_day.get("2026-06-10")["n"] == 2


def test_summarize_deals_empty_input():
    summary = summarize_deals([])
    assert summary["closed_deals"] == 0
    assert summary["total_pnl"] == 0.0
    assert summary["by_symbol"] == {}
    assert summary["by_strategy"] == {}
    assert summary["by_day"] == {}


def test_retired_strategy_fills_flags_only_retired_set():
    # Craft ack lines that look like the EA output
    headers = "id,time,status,detail,symbol,side,volume,price"
    lines = [
        "1,2026-06-10 10:00:00,AUTO_FILLED,XAUUSD strategy MACD_trend smc=4 pda=0.32,XAUUSD,BUY,0.01,2000.00",
        "2,2026-06-10 11:00:00,AUTO_FILLED,XAUUSD strategy QuickMomentum_EMA_cross smc=3 pda=0.30,XAUUSD,BUY,0.01,60000.00",
        "3,2026-06-10 12:00:00,AUTO_FILLED,XAUUSD strategy RSI_reversion smc=2 pda=0.50,XAUUSD,SELL,0.01,60000.00",
        "4,2026-06-10 13:00:00,AUTO_FILLED,XAUUSD strategy ATR_impulse smc=3 pda=0.40,XAUUSD,BUY,0.01,60000.00",
        "5,2026-06-10 14:00:00,AUTO_FILLED,XAUUSD strategy Momentum_trend smc=2 pda=0.45,XAUUSD,BUY,0.01,60000.00",
        "6,2026-06-10 15:00:00,AUTO_FILLED,XAUUSD strategy MACD_trend smc=2 pda=0.40,XAUUSD,BUY,0.01,60000.00",
    ]
    result = retired_strategy_fills(lines, recent=80)
    # XAU RSI_reversion is retired; active impulse/momentum examples are not.
    counts = result["counts"]
    assert "XAUUSD:RSI_reversion" in counts
    assert "XAUUSD:MACD_trend" not in counts
    assert "XAUUSD:ATR_impulse" not in counts
    assert "XAUUSD:Momentum_trend" not in counts
    assert "XAUUSD:QuickMomentum_EMA_cross" not in counts
    assert len(result["recent"]) == 1


def test_retired_strategy_fills_empty_input():
    result = retired_strategy_fills([], recent=80)
    assert result["counts"] == {}
    assert result["recent"] == []


def test_retired_set_is_not_empty():
    # Sanity: the retired set should match the AGENTS.md policy
    assert ('XAUUSD', 'RSI_reversion') in RETIRED_AUTO_STRATEGIES
