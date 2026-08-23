from __future__ import annotations

import pytest

from aiquanttrader.backtest.strategies.xau_live_signals import (
    XauLiveSignalParams,
    XauLiveSignalStrategy,
    _enabled_xau_decision,
)


def test_enabled_xau_decision_prefers_atr_impulse_label():
    feature = _feature(
        current=101.0,
        previous=100.0,
        previous_high=100.5,
        momentum3=0.002,
        quick_momentum_long=True,
    )

    assert _enabled_xau_decision(feature, XauLiveSignalParams()) == (
        "BUY",
        "ATR_impulse",
    )


def test_enabled_xau_decision_includes_quick_momentum_path():
    feature = _feature(quick_momentum_long=True)

    assert _enabled_xau_decision(feature, XauLiveSignalParams()) == (
        "BUY",
        "Momentum_trend",
    )


def test_enabled_xau_decision_includes_three_bar_momentum_path():
    feature = _feature(
        current=101.0,
        previous=101.0,
        previous_high=102.0,
        ema_trend=100.0,
        momentum3=0.002,
    )

    assert _enabled_xau_decision(feature, XauLiveSignalParams()) == (
        "BUY",
        "Momentum_trend",
    )


def test_enabled_xau_decision_mirrors_long_before_short_precedence():
    feature = _feature(
        quick_momentum_long=True,
        quick_momentum_short=True,
    )

    assert _enabled_xau_decision(feature, XauLiveSignalParams()) == (
        "BUY",
        "Momentum_trend",
    )


def test_live_signal_strategy_emits_protected_signal():
    closes = [2000.0 + (0.05 if idx % 2 else -0.05) for idx in range(60)]
    bars = [
        {
            "time": 1_700_000_000 + idx * 60,
            "open": closes[idx],
            "high": closes[idx] + 0.2,
            "low": closes[idx] - 0.2,
            "close": closes[idx],
            "volume": 1.0,
        }
        for idx in range(60)
    ]
    bars[-1] = {
        **bars[-1],
        "open": bars[-2]["close"],
        "high": bars[-2]["high"] + 2.0,
        "low": bars[-2]["low"],
        "close": bars[-2]["high"] + 1.0,
    }
    strategy = XauLiveSignalStrategy(timeframe="M1")
    signal = None
    history: list[dict] = []
    for idx, bar in enumerate(bars):
        history.append(bar)
        signal = strategy.on_bar(
            idx=idx,
            bar=bar,
            history=history,
            open_positions=[],
            equity=1_000_000.0,
            day_closed_pnl=0.0,
        )

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.comment == "ATR_impulse"
    assert signal.sl_distance is not None and signal.sl_distance > 0.0
    assert signal.tp_distance == pytest.approx(signal.sl_distance * 2.4)


def _feature(**overrides):
    feature = {
        "atr": 1.0,
        "rsi": 50.0,
        "current": 100.0,
        "previous": 100.0,
        "previous_high": 101.0,
        "previous_low": 99.0,
        "ema_trend": 100.0,
        "momentum3": 0.0,
        "quick_momentum_long": False,
        "quick_momentum_short": False,
    }
    feature.update(overrides)
    return feature
