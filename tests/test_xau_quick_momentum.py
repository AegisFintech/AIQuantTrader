from __future__ import annotations

import math

import pytest

from finrobot.backtest import (
    Backtester,
    BacktestConfig,
    FillConfig,
    PositionSizer,
    XauQuickMomentumStrategy,
)
from finrobot.backtest.parity_replay import (
    ParityReplayConfig,
    _ReplayVolumeSizer,
    run_parity_replay,
)
from finrobot.backtest.strategies.xau_gates import (
    atr_series,
    compute_xau_features,
    ema,
    macd,
    rsi,
)


def test_ema_warmup_and_convergence():
    values = [float(idx) for idx in range(1, 8)]

    result = ema(values, 3)

    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3:] == pytest.approx([3.0, 4.0, 5.0, 6.0], abs=1e-6)


def test_rsi_warmup_returns_none():
    values = [1, 2, 3, 2, 1, 2]

    result = rsi(values, period=5)

    assert result[:5] == [None] * 5
    assert result[5] == pytest.approx(60.0, abs=1e-6)


def test_macd_returns_three_series():
    values = [float(idx) for idx in range(1, 50)]

    main, signal, histogram = macd(values)

    assert len(main) == len(values)
    assert len(signal) == len(values)
    assert len(histogram) == len(values)
    for main_value, signal_value, hist_value in zip(main, signal, histogram):
        if main_value is not None and signal_value is not None:
            assert hist_value == pytest.approx(main_value - signal_value)
        else:
            assert hist_value is None


def test_atr_warmup():
    bars = [
        _bar(idx, close=100.0 + idx, high=101.0 + idx, low=99.0 + idx)
        for idx in range(6)
    ]

    result = atr_series(bars, period=3)

    assert result[:3] == [None, None, None]
    assert result[3] == pytest.approx(2.0, abs=1e-6)


def test_compute_xau_features_quick_momentum_long_fires():
    bars = _quick_momentum_long_bars()

    features = compute_xau_features(bars)

    assert features[-1]["quick_momentum_long"] is True
    assert features[-1]["quick_momentum_short"] is False


def test_compute_xau_features_quick_momentum_short_fires():
    bars = _quick_momentum_short_bars()

    features = compute_xau_features(bars)

    assert features[-1]["quick_momentum_short"] is True
    assert features[-1]["quick_momentum_long"] is False


def test_xau_quick_momentum_emits_buy_with_sl_tp():
    bars = _quick_momentum_long_bars()
    strategy = XauQuickMomentumStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)
    feature = compute_xau_features(bars)[-1]
    expected_sl = max(
        feature["atr"] * 1.2,
        max(feature["current"] * 0.00045, 2.0),
    )

    assert signal.action == "BUY"
    assert signal.strategy == "XauQuickMomentum"
    assert signal.comment == "XauQuickMomentum_EMA_cross"
    assert signal.sl_distance == pytest.approx(expected_sl, abs=1e-6)
    assert signal.tp_distance == pytest.approx(expected_sl * 1.8, abs=1e-6)


def test_xau_quick_momentum_holds_when_no_signal():
    bars = [_bar(idx, close=2000.0) for idx in range(60)]
    strategy = XauQuickMomentumStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)

    assert signal.action == "HOLD"
    assert signal.strategy == "XauQuickMomentum"


def test_xau_quick_momentum_runs_through_backtester():
    bars = _quick_momentum_long_bars()
    signal_bar = len(bars) - 1
    feature = compute_xau_features(bars)[signal_bar]
    sl_distance = max(
        feature["atr"] * 1.2,
        max(feature["current"] * 0.00045, 2.0),
    )
    tp_distance = sl_distance * 1.8

    result = Backtester(_backtest_config()).run(
        strategy=XauQuickMomentumStrategy(),
        bars=bars,
    )

    assert len(result.trades) >= 1
    trade = next(trade for trade in result.trades if trade["entry_bar_idx"] == signal_bar)
    assert trade["side"] == "BUY"
    assert trade["sl"] == pytest.approx(trade["entry_price"] - sl_distance, abs=1e-6)
    assert trade["tp"] == pytest.approx(trade["entry_price"] + tp_distance, abs=1e-6)


def test_xau_quick_momentum_synthesized_parity():
    bars = _quick_momentum_long_bars()
    signal_bar = len(bars) - 1
    close = bars[signal_bar]["close"]
    decisions = [
        {
            "bar_idx": signal_bar,
            "action": "BUY",
            "side": "BUY",
            "volume": 0.01,
            "price": close,
        }
    ]

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=ParityReplayConfig(
            from_date="2026-05-01",
            to_date="2026-05-02",
            symbol="XAUUSD",
            fill_tolerance_points=0.0,
            bar_match_window=0,
            run_id="xau-quick-momentum-synth",
        ),
        backtest_config=_backtest_config(),
        strategy=XauQuickMomentumStrategy(),
        volume_sizer=_ReplayVolumeSizer(decisions),
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 1
    assert report.n_mismatched == 0


def _run_strategy_to_bar(
    strategy: XauQuickMomentumStrategy,
    bars: list[dict],
    target_idx: int,
):
    signal = None
    history: list[dict] = []
    for idx, bar in enumerate(bars[: target_idx + 1]):
        history.append(bar)
        signal = strategy.on_bar(
            idx=idx,
            bar=bar,
            history=history,
            open_positions=[],
            equity=10000.0,
            day_closed_pnl=0.0,
        )
    assert signal is not None
    return signal


def _quick_momentum_long_bars() -> list[dict]:
    closes = _synthetic_quick_momentum_closes(
        direction=1.0,
        trend_step=0.0,
        amplitude=0.2,
        divisor=7.0,
        pullback=0.5,
        rebound=0.2,
    )
    return [_bar(idx, close=close) for idx, close in enumerate(closes)]


def _quick_momentum_short_bars() -> list[dict]:
    closes = _synthetic_quick_momentum_closes(
        direction=-1.0,
        trend_step=0.0,
        amplitude=0.2,
        divisor=1.7,
        pullback=0.2,
        rebound=0.5,
    )
    return [_bar(idx, close=close) for idx, close in enumerate(closes)]


def _synthetic_quick_momentum_closes(
    *,
    direction: float,
    trend_step: float,
    amplitude: float,
    divisor: float,
    pullback: float,
    rebound: float,
) -> list[float]:
    """Return 60 hand-crafted closes whose final bar crosses fast EMA."""

    closes = []
    for idx in range(55):
        trend = direction * idx * trend_step
        oscillation = math.sin(idx / divisor) * amplitude
        closes.append(2000.0 + trend + oscillation)

    base = closes[-1]
    closes.extend(
        [
            base - direction * pullback,
            base - direction * pullback * 1.15,
            base - direction * pullback * 0.85,
            base - direction * pullback * 0.55,
            base + direction * rebound,
        ]
    )
    return closes


def _backtest_config() -> BacktestConfig:
    return BacktestConfig(
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        ),
    )


def _bar(idx: int, *, close: float, high: float | None = None, low: float | None = None):
    return {
        "time": f"2026-05-01 10:{idx:02d}:00",
        "open": close,
        "high": close + 1.0 if high is None else high,
        "low": close - 1.0 if low is None else low,
        "close": close,
        "volume": 1.0,
    }
