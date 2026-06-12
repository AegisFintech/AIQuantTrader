from __future__ import annotations

import pytest

from finrobot.backtest import (
    Backtester,
    BacktestConfig,
    BreakEvenConfig,
    FillConfig,
    PositionSizer,
    Signal,
    Strategy,
)


def test_break_even_disabled_does_not_move_sl():
    result = Backtester(_config()).run(
        strategy=_OneShot("BUY"),
        bars=_bars([100.0, 120.0, 130.0]),
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(80.0)
    assert trade["break_even_applied"] is False


def test_break_even_moves_sl_to_entry_plus_extra_on_buy():
    result = Backtester(_config(break_even=BreakEvenConfig(enabled=True))).run(
        strategy=_OneShot("BUY"),
        bars=_bars([100.0, 120.0, 130.0]),
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(110.0)
    assert trade["break_even_applied"] is True


def test_break_even_moves_sl_to_entry_minus_extra_on_sell():
    result = Backtester(_config(break_even=BreakEvenConfig(enabled=True))).run(
        strategy=_OneShot("SELL", tp_distance=40.0),
        bars=_bars([100.0, 80.0, 70.0]),
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(90.0)
    assert trade["break_even_applied"] is True


def test_break_even_only_moves_once():
    result = Backtester(_config(break_even=BreakEvenConfig(enabled=True))).run(
        strategy=_OneShot("BUY"),
        bars=_bars([100.0, 120.0, 135.0, 150.0]),
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(110.0)
    assert trade["break_even_applied"] is True


def test_break_even_threshold_at_rr_ratio_2():
    first = Backtester(
        _config(break_even=BreakEvenConfig(enabled=True, rr_ratio=2.0))
    ).run(
        strategy=_OneShot("BUY"),
        bars=_bars([100.0, 120.0]),
    )
    second = Backtester(
        _config(break_even=BreakEvenConfig(enabled=True, rr_ratio=2.0))
    ).run(
        strategy=_OneShot("BUY"),
        bars=_bars([100.0, 120.0, 140.0]),
    )

    assert first.trades[0]["sl"] == pytest.approx(80.0)
    assert first.trades[0]["break_even_applied"] is False
    assert second.trades[0]["sl"] == pytest.approx(110.0)
    assert second.trades[0]["break_even_applied"] is True


def test_break_even_does_not_trigger_for_no_sl_position():
    result = Backtester(_config(break_even=BreakEvenConfig(enabled=True))).run(
        strategy=_OneShot("BUY", sl_distance=0.0, tp_distance=100.0),
        bars=_bars([100.0, 130.0, 140.0]),
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(0.0)
    assert trade["break_even_applied"] is False


def test_break_even_does_not_move_sl_backwards():
    result = Backtester(_config(break_even=BreakEvenConfig(enabled=True))).run(
        strategy=_OneShot("BUY"),
        bars=[
            _bar(0, close=100.0, high=101.0, low=99.0),
            _bar(1, close=120.0, high=120.5, low=100.0),
            _bar(2, close=100.0, high=101.0, low=99.0),
            _bar(3, close=105.0, high=106.0, low=104.0),
        ],
    )

    trade = result.trades[0]
    assert trade["sl"] == pytest.approx(110.0)
    assert trade["break_even_applied"] is True


class _OneShot(Strategy):
    name = "OneShotBreakEven"

    def __init__(
        self,
        action: str,
        *,
        sl_distance: float = 20.0,
        tp_distance: float = 100.0,
    ):
        self.action = action
        self.sl_distance = float(sl_distance)
        self.tp_distance = float(tp_distance)
        self.sent = False

    def on_bar(self, **kwargs) -> Signal:
        if self.sent:
            return Signal(action="HOLD", strategy=self.name)
        self.sent = True
        return Signal(
            action=self.action,
            sl_distance=self.sl_distance,
            tp_distance=self.tp_distance,
            strategy=self.name,
            comment="break_even_test",
        )


def _config(*, break_even: BreakEvenConfig | None = None) -> BacktestConfig:
    return BacktestConfig(
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        break_even=break_even or BreakEvenConfig(),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        ),
    )


def _bars(closes: list[float]) -> list[dict]:
    return [
        _bar(idx, close=close, high=close + 0.5, low=close - 0.5)
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
