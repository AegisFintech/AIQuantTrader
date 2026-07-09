from __future__ import annotations

import pytest

from finrobot.backtest import (
    Backtester,
    BacktestConfig,
    BuyAndHold,
    FillConfig,
    MetricsReport,
    Position,
    PositionSizer,
    Signal,
    Strategy,
    compute_metrics,
)


def test_buy_and_hold_on_rising_series_has_positive_pnl():
    result = Backtester(_config()).run(
        strategy=BuyAndHold(), bars=_bars([100.0, 105.0, 110.0])
    )

    assert len(result.trades) == 1
    assert result.trades[0]["pnl"] > 0
    assert result.final_equity > result.initial_equity


def test_buy_and_hold_on_falling_series_has_negative_pnl():
    result = Backtester(_config()).run(
        strategy=BuyAndHold(), bars=_bars([110.0, 105.0, 100.0])
    )

    assert len(result.trades) == 1
    assert result.trades[0]["pnl"] < 0


def test_buy_and_hold_with_zero_equity_rejects_signal():
    result = Backtester(_config(initial_equity=0.0)).run(
        strategy=BuyAndHold(), bars=_bars([100.0, 101.0])
    )

    assert result.trades == []
    assert result.rejected_signals == 1


def test_buy_signal_with_no_sl_is_rejected_for_regular_strategy():
    result = Backtester(_config()).run(
        strategy=OneShotSignal(Signal(action="BUY", tp_distance=10.0, strategy="NoSL")),
        bars=_bars([100.0, 105.0]),
    )

    assert result.trades == []
    assert result.rejected_signals == 1


def test_buy_with_zero_sl_and_tp_opens_and_closes_at_tp():
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=110.0, high=111.0, low=100.0),
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="BUY", sl_distance=0.0, tp_distance=10.0, strategy="TP")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "tp"
    assert result.trades[0]["exit_price"] == pytest.approx(110.0)


def test_buy_with_sl_closes_at_sl_when_next_bar_drops():
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=95.0, high=101.0, low=94.0),
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="BUY", sl_distance=5.0, tp_distance=10.0, strategy="SL")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "sl"
    assert result.trades[0]["exit_price"] == pytest.approx(95.0)
    assert result.trades[0]["pnl"] < 0


def test_buy_and_sell_on_consecutive_bars_opens_two_positions():
    strategy = SequenceStrategy(
        [
            Signal(action="BUY", sl_distance=20.0, strategy="Seq"),
            Signal(action="SELL", sl_distance=20.0, strategy="Seq"),
        ]
    )

    result = Backtester(_config(max_positions_per_symbol=2)).run(
        strategy=strategy, bars=_bars([100.0, 101.0, 102.0])
    )

    assert len(result.trades) == 2
    assert [trade["side"] for trade in result.trades] == ["BUY", "SELL"]


def test_position_sizer_returns_zero_after_daily_loss_cap():
    sizer = PositionSizer(
        risk_per_trade_fraction=0.001,
        daily_loss_cap_fraction=0.01,
        max_lot_per_trade=1.0,
        max_positions_per_symbol=2,
    )

    lot = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=10.0,
        open_positions=[],
        today_closed_pnl=-100.0,
    )

    assert lot == 0.0


def test_position_sizer_returns_zero_at_symbol_position_cap():
    sizer = PositionSizer(
        risk_per_trade_fraction=0.001,
        daily_loss_cap_fraction=0.01,
        max_lot_per_trade=1.0,
        max_positions_per_symbol=1,
    )
    open_positions = [
        Position(
            symbol="XAUUSD",
            side="BUY",
            volume=0.1,
            entry_price=100.0,
            entry_time=0,
            sl=90.0,
            tp=110.0,
            magic=1,
        )
    ]

    lot = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=10.0,
        open_positions=open_positions,
        today_closed_pnl=0.0,
    )

    assert lot == 0.0


def test_backtester_is_deterministic_for_same_inputs():
    config = _config()
    bars = _bars([100.0, 101.0, 102.0, 103.0])

    first = Backtester(config).run(strategy=BuyAndHold(), bars=bars)
    second = Backtester(config).run(strategy=BuyAndHold(), bars=bars)

    assert first == second


def test_empty_bars_returns_unchanged_equity_and_no_trades():
    result = Backtester(_config()).run(strategy=BuyAndHold(), bars=[])

    assert result.bars == 0
    assert result.trades == []
    assert result.final_equity == pytest.approx(result.initial_equity)


def test_end_to_end_buy_and_hold_metrics_shape_on_100_bars():
    result = Backtester(_config()).run(
        strategy=BuyAndHold(), bars=_bars([100.0 + idx for idx in range(100)])
    )
    report = compute_metrics(result)

    assert isinstance(report, MetricsReport)
    assert report.n_trades == 1
    assert report.final_equity == pytest.approx(result.final_equity)
    assert len(result.equity_curve) == 100


def test_buy_sl_triggers_on_gap_open_below_sl():
    """A bar that gaps entirely below SL must still exit at SL price."""
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=85.0, high=88.0, low=84.0),  # gap below SL of 95
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="BUY", sl_distance=5.0, tp_distance=20.0, strategy="GapSL")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "sl"


def test_sell_sl_triggers_on_gap_open_above_sl():
    """A bar that gaps entirely above SL for a SELL must still trigger SL."""
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=115.0, high=116.0, low=112.0),  # gap above SL of 105
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="SELL", sl_distance=5.0, tp_distance=20.0, strategy="GapSL")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "sl"


def test_buy_tp_triggers_on_gap_open_above_tp():
    """A bar that gaps entirely above TP must still trigger TP."""
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=125.0, high=126.0, low=122.0),  # gap above TP of 110
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="BUY", sl_distance=5.0, tp_distance=10.0, strategy="GapTP")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "tp"


def test_sell_tp_triggers_on_gap_open_below_tp():
    """A bar that gaps entirely below TP for a SELL must trigger TP."""
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=78.0, high=79.0, low=77.0),  # gap below TP of 90
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="SELL", sl_distance=5.0, tp_distance=10.0, strategy="GapTP")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "tp"


def test_both_sl_and_tp_in_bar_exits_at_sl():
    """When both SL and TP are within a bar's range, SL takes priority (conservative)."""
    bars = [
        _bar(0, close=100.0, high=101.0, low=99.0),
        _bar(1, close=100.0, high=111.0, low=94.0),  # both SL=95 and TP=110 in range
    ]

    result = Backtester(_config()).run(
        strategy=OneShotSignal(
            Signal(action="BUY", sl_distance=5.0, tp_distance=10.0, strategy="Both")
        ),
        bars=bars,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["exit_reason"] == "sl"


class OneShotSignal(Strategy):
    name = "OneShot"

    def __init__(self, signal: Signal):
        self.signal = signal
        self.sent = False

    def on_bar(self, **kwargs) -> Signal:
        if self.sent:
            return Signal(action="HOLD", strategy=self.name)
        self.sent = True
        return self.signal


class SequenceStrategy(Strategy):
    name = "Sequence"

    def __init__(self, signals: list[Signal]):
        self.signals = signals

    def on_bar(self, *, idx: int, **kwargs) -> Signal:
        if idx < len(self.signals):
            return self.signals[idx]
        return Signal(action="HOLD", strategy=self.name)


def _config(
    *,
    initial_equity: float = 10000.0,
    max_positions_per_symbol: int = 2,
) -> BacktestConfig:
    return BacktestConfig(
        initial_equity=initial_equity,
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=max_positions_per_symbol,
        ),
    )


def _bars(closes: list[float]) -> list[dict]:
    return [
        _bar(idx, close=close, high=close + 1.0, low=close - 1.0)
        for idx, close in enumerate(closes)
    ]


def _bar(idx: int, *, close: float, high: float, low: float) -> dict:
    return {
        "time": 1_700_000_000 + idx * 60,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
    }
