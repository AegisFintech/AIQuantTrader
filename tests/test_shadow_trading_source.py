from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_entry_pause_shadow_branch_returns_before_any_order_submission():
    source = (ROOT / "broker" / "mt5" / "AIQuantTraderBridgeEA.mq5").read_text()
    manage = source.split("void ManageAutoSymbol(string symbol, int idx) {", 1)[1].split(
        "void WritePositions()", 1
    )[0]

    shadow_branch = manage.split("if(shadowMode) {", 1)[1].split(
        "trade.SetExpertMagicNumber", 1
    )[0]
    assert "AppendShadowSignal(" in shadow_branch
    assert "return;" in shadow_branch
    assert "trade.Buy(" not in shadow_branch
    assert "trade.Sell(" not in shadow_branch


def test_market_commands_remain_rejected_during_entry_pause():
    source = (ROOT / "broker" / "mt5" / "AIQuantTraderBridgeEA.mq5").read_text()

    assert 'if(action == "MARKET" && IsEntryPauseActive())' in source
    assert '"Entry pause is active"' in source


def test_shadow_file_contains_execution_terms_needed_for_forward_resolution():
    source = (ROOT / "broker" / "mt5" / "BridgeIO.mqh").read_text()

    for field in (
        "signal_bar_time",
        "profile",
        "strategy",
        "entry",
        "sl",
        "tp",
        "smc",
        "pda",
        "spread_points",
        "dynamic_break_even",
        "break_even_rr_ratio",
        "break_even_extra_points",
    ):
        assert field in source
