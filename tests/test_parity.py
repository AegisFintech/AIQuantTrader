from __future__ import annotations

import pytest

from aiquanttrader.backtest import BacktestConfig, BacktestResult, compare_decisions


def test_compare_decisions_empty_inputs_returns_zero_decisions():
    report = compare_decisions(_result([]), [])

    assert report.n_decisions == 0
    assert report.n_matched == 0
    assert report.n_mismatched == 0
    assert report.match_rate == 0.0


def test_compare_decisions_matched_trade_counts_as_match():
    report = compare_decisions(
        _result([_trade(bar_idx=3, side="BUY", volume=0.1)]),
        [{"bar_idx": 3, "action": "BUY", "side": "BUY", "volume": 0.1}],
    )

    assert report.n_matched == 1
    assert report.n_mismatched == 0


def test_compare_decisions_mismatched_side_reports_detail():
    report = compare_decisions(
        _result([_trade(bar_idx=3, side="BUY", volume=0.1)]),
        [{"bar_idx": 3, "action": "SELL", "side": "SELL", "volume": 0.1}],
    )

    assert report.n_mismatched == 1
    assert "side mismatch" in report.mismatches[0]["detail"]


def test_compare_decisions_missing_backtester_trade_reports_mismatch():
    report = compare_decisions(
        _result([]),
        [{"bar_idx": 3, "action": "BUY", "side": "BUY", "volume": 0.1}],
    )

    assert report.n_mismatched == 1
    assert "no backtester trade" in report.mismatches[0]["detail"]


def test_compare_decisions_match_rate_is_bounded_and_derived():
    report = compare_decisions(
        _result([_trade(bar_idx=1, side="BUY", volume=0.1)]),
        [
            {"bar_idx": 1, "action": "BUY", "side": "BUY", "volume": 0.1},
            {"bar_idx": 10, "action": "SELL", "side": "SELL", "volume": 0.1},
        ],
    )

    assert 0.0 <= report.match_rate <= 1.0
    assert report.match_rate == pytest.approx(report.n_matched / report.n_decisions)


def _result(trades: list[dict]) -> BacktestResult:
    return BacktestResult(
        config=BacktestConfig(),
        strategy_name="Parity",
        bars=0,
        start_time=0,
        end_time=0,
        initial_equity=10000.0,
        final_equity=10000.0,
        trades=trades,
        equity_curve=[],
        open_positions_at_end=[],
        rejected_signals=0,
    )


def _trade(*, bar_idx: int, side: str, volume: float) -> dict:
    return {"bar_idx": bar_idx, "action": side, "side": side, "volume": volume}
