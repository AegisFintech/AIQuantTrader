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
    read_shadow_bars,
    resolve_shadow_signals,
    retired_strategy_fills,
    summarize_shadow_signals,
    summarize_deals,
)


def _deal(
    symbol,
    position_id,
    entry,
    profit,
    comment="",
    time="2026-06-10 10:00:00",
    deal_type=1,
    commission="0.0",
):
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
        "commission": str(commission),
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
        _deal("XAUUSD", 1, entry=0, profit=0.0, comment="AIQuantTrader_XAUUSD_MACD_trend", time="2026-06-10 10:00:00"),
        _deal("XAUUSD", 1, entry=1, profit=10.0, time="2026-06-10 11:00:00"),
        _deal("XAUUSD", 2, entry=0, profit=0.0, comment="AIQuantTrader_XAUUSD_QuickMomentum_EMA_cross", time="2026-06-10 12:00:00"),
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
    # The report keys strategies by the full entry comment (e.g. "AIQuantTrader_XAUUSD_MACD_trend").
    assert "XAUUSD:AIQuantTrader_XAUUSD_MACD_trend" in by_strat
    assert "XAUUSD:AIQuantTrader_XAUUSD_QuickMomentum_EMA_cross" in by_strat
    by_day = summary["by_day"]
    assert by_day.get("2026-06-10")["n"] == 2


def test_summarize_deals_includes_entry_and_exit_commission():
    rows = [
        _deal(
            "XAUUSD",
            1,
            entry=0,
            profit=0.0,
            commission="-0.35",
            comment="AIQuantTrader_XAUUSD_ATR_impulse",
            time="2026-06-10 10:00:00",
        ),
        _deal("XAUUSD", 1, entry=1, profit=10.0, commission="-0.35", time="2026-06-10 11:00:00"),
    ]
    summary = summarize_deals(rows)
    assert summary["closed_deals"] == 1
    assert summary["total_pnl"] == 9.3
    assert summary["by_symbol"]["XAUUSD"]["expectancy"] == 9.3


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


def _shadow_signal(
    signal_id: str,
    side: str,
    *,
    signal_bar_time: int = 1000,
    entry: float = 100.0,
    sl: float = 99.0,
    tp: float = 102.0,
    spread_points: float = 5.0,
    dynamic_break_even: bool = False,
    break_even_rr_ratio: float = 1.0,
    break_even_extra_points: float = 10.0,
) -> dict:
    return {
        "signal_id": signal_id,
        "time": "2026.07.24 10:00:01",
        "ts_server": str(signal_bar_time + 60),
        "signal_bar_time": str(signal_bar_time),
        "symbol": "XAUUSD",
        "profile": "compiled_defaults",
        "side": side,
        "strategy": "ATR_impulse",
        "volume": "1.0",
        "entry": str(entry),
        "sl": str(sl),
        "tp": str(tp),
        "smc": "4",
        "pda": "0.2",
        "spread_points": str(spread_points),
        "dynamic_break_even": "1" if dynamic_break_even else "0",
        "break_even_rr_ratio": str(break_even_rr_ratio),
        "break_even_extra_points": str(break_even_extra_points),
    }


def _bar(timestamp: int, high: float, low: float, close: float = 100.0) -> dict:
    return {"time": timestamp, "open": 100.0, "high": high, "low": low, "close": close, "volume": 10.0}


def test_resolve_shadow_signals_uses_later_bars_and_round_trip_commission():
    rows = [_shadow_signal("buy-1", "BUY")]
    bars = [
        _bar(1000, high=999.0, low=1.0),  # Signal bar must never resolve its own entry.
        _bar(1060, high=102.1, low=99.5, close=102.0),
    ]

    outcomes = resolve_shadow_signals(rows, bars)

    assert len(outcomes) == 1
    assert outcomes[0]["outcome"] == "tp"
    assert outcomes[0]["exit_time"] == 1060
    assert outcomes[0]["gross_pnl"] == 200.0
    assert outcomes[0]["commission"] == 7.0
    assert outcomes[0]["net_pnl"] == 193.0


def test_resolve_shadow_signals_is_conservative_when_sl_and_tp_touch_same_bar():
    rows = [_shadow_signal("buy-both", "BUY")]
    bars = [_bar(1060, high=102.5, low=98.5)]

    outcome = resolve_shadow_signals(rows, bars)[0]

    assert outcome["outcome"] == "sl"
    assert outcome["net_pnl"] == -107.0


def test_resolve_shadow_sell_uses_ask_side_spread_for_exit_triggers():
    rows = [_shadow_signal("sell-1", "SELL", sl=101.0, tp=98.0)]
    bars = [_bar(1060, high=100.2, low=97.94)]

    outcome = resolve_shadow_signals(rows, bars)[0]

    assert outcome["outcome"] == "tp"
    assert outcome["net_pnl"] == 193.0


def test_resolve_shadow_signals_applies_break_even_from_the_next_bar():
    rows = [
        _shadow_signal(
            "buy-be",
            "BUY",
            tp=103.0,
            dynamic_break_even=True,
            break_even_rr_ratio=1.0,
            break_even_extra_points=10.0,
        )
    ]
    bars = [
        _bar(1060, high=101.1, low=99.5, close=101.0),
        _bar(1120, high=101.0, low=100.0, close=100.1),
    ]

    outcome = resolve_shadow_signals(rows, bars)[0]

    assert outcome["outcome"] == "be"
    assert outcome["exit_price"] == 100.1
    assert outcome["net_pnl"] == 3.0


def test_summarize_shadow_signals_deduplicates_ids_and_keeps_open_signals():
    closed = _shadow_signal("same-id", "BUY")
    duplicate = dict(closed)
    open_signal = _shadow_signal("open-id", "BUY", signal_bar_time=2000)
    report = summarize_shadow_signals(
        [closed, duplicate, open_signal],
        [_bar(1060, high=102.1, low=99.5)],
    )

    assert report["total"] == {
        "signals": 2,
        "resolved": 1,
        "open": 1,
        "wins": 1,
        "losses": 0,
        "win_rate": 1.0,
        "net_pnl": 193.0,
        "profit_factor": "inf",
        "expectancy": 193.0,
    }
    assert report["assumptions"]["both_hit_rule"] == "stop_first"
    assert report["assumptions"]["dynamic_break_even_ordering"] == "activate_after_surviving_bar"


def test_read_shadow_bars_accepts_epoch_and_text_timestamps(tmp_path):
    path = tmp_path / "bars.tsv"
    path.write_text(
        "1000\t100\t101\t99\t100.5\t12\n"
        "2026-07-24 10:00\t100.5\t102\t100\t101.5\t14\n"
    )

    bars = read_shadow_bars(path)

    assert len(bars) == 2
    assert bars[0]["time"] == 1000
    assert bars[1]["high"] == 102.0
