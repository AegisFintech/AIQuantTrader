from __future__ import annotations

import pytest

from finrobot.backtest import (
    Backtester,
    BacktestConfig,
    FillConfig,
    PositionSizer,
    XauAtrImpulseStrategy,
    XauQuickMomentumParams,
    XauQuickMomentumStrategy,
)
from finrobot.backtest.parity_replay import (
    ParityReplayConfig,
    _ReplayVolumeSizer,
    run_parity_replay,
)
from finrobot.backtest.strategies._xau_state import XauM5RollingFeatureState


def test_xau_rolling_state_no_regression():
    bars = _atr_impulse_long_bars()
    params = XauQuickMomentumParams()
    state = XauM5RollingFeatureState(params)
    expected_features = [
        state.update(idx, bar)
        for idx, bar in enumerate(bars)
    ]
    strategy = XauQuickMomentumStrategy(params=params)

    _run_strategy_to_bar(strategy, bars, len(bars) - 1)

    assert strategy._features == expected_features


def test_xau_atr_impulse_long_fires_on_breakout():
    bars = _atr_impulse_long_bars()
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)
    feature = strategy._features[-1]
    expected_sl, expected_tp = _expected_distances(feature)

    assert signal.action == "BUY"
    assert signal.strategy == "XauAtrImpulse"
    assert signal.comment == "ATR_impulse"
    assert signal.sl_distance == pytest.approx(expected_sl, abs=1e-6)
    assert signal.tp_distance == pytest.approx(expected_tp, abs=1e-6)


def test_xau_atr_impulse_m1_uses_previous_m1_bar_for_breakout_context():
    bars = _atr_impulse_long_bars()
    strategy = XauAtrImpulseStrategy(timeframe="M1")

    _run_strategy_to_bar(strategy, bars, len(bars) - 1)
    feature = strategy._features[-1]

    assert feature["previous_high"] == pytest.approx(bars[-2]["high"])
    assert feature["previous_low"] == pytest.approx(bars[-2]["low"])
    assert "m5_bucket_start" not in feature


def test_xau_atr_impulse_short_fires_on_breakout():
    bars = _atr_impulse_short_bars()
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)
    feature = strategy._features[-1]
    expected_sl, expected_tp = _expected_distances(feature)

    assert signal.action == "SELL"
    assert signal.strategy == "XauAtrImpulse"
    assert signal.comment == "ATR_impulse"
    assert signal.sl_distance == pytest.approx(expected_sl, abs=1e-6)
    assert signal.tp_distance == pytest.approx(expected_tp, abs=1e-6)


def test_xau_atr_impulse_rsi_ceiling_blocks_long():
    bars = _atr_impulse_long_bars(overbought=True)
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)

    assert strategy._features[-1]["rsi"] >= 80.0
    assert signal.action == "HOLD"
    assert signal.strategy == "XauAtrImpulse"


def test_xau_atr_impulse_rsi_floor_blocks_short():
    bars = _atr_impulse_short_bars(oversold=True)
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)

    assert strategy._features[-1]["rsi"] <= 20.0
    assert signal.action == "HOLD"
    assert signal.strategy == "XauAtrImpulse"


def test_xau_atr_impulse_breakout_filter():
    bars = _atr_impulse_long_bars(breakout=False)
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)
    feature = strategy._features[-1]

    assert feature["current"] <= feature["previous_high"]
    assert bars[-1]["high"] <= feature["previous_high"]
    assert (feature["current"] - feature["previous"]) > feature["atr"] * 0.12
    assert feature["rsi"] < 80.0
    assert signal.action == "HOLD"
    assert signal.strategy == "XauAtrImpulse"


def test_xau_atr_impulse_runs_through_backtester():
    bars = _atr_impulse_long_bars()
    signal_bar = len(bars) - 1
    state = XauM5RollingFeatureState(XauQuickMomentumParams())
    features = [state.update(idx, bar) for idx, bar in enumerate(bars)]
    expected_sl, expected_tp = _expected_distances(features[signal_bar])

    result = Backtester(_backtest_config()).run(
        strategy=XauAtrImpulseStrategy(),
        bars=bars,
    )

    assert len(result.trades) >= 1
    trade = next(trade for trade in result.trades if trade["entry_bar_idx"] == signal_bar)
    assert trade["side"] == "BUY"
    assert trade["comment"] == "ATR_impulse"
    assert trade["sl"] == pytest.approx(trade["entry_price"] - expected_sl, abs=1e-6)
    assert trade["tp"] == pytest.approx(trade["entry_price"] + expected_tp, abs=1e-6)


def test_xau_atr_impulse_synthesized_parity():
    bars = _atr_impulse_long_bars()
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
            run_id="xau-atr-impulse-synth",
        ),
        backtest_config=_backtest_config(),
        strategy=XauAtrImpulseStrategy(),
        volume_sizer=_ReplayVolumeSizer(decisions),
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 1
    assert report.n_mismatched == 0


def test_xau_atr_impulse_uses_atr_impulse_comment():
    bars = _atr_impulse_long_bars()
    strategy = XauAtrImpulseStrategy()

    signal = _run_strategy_to_bar(strategy, bars, len(bars) - 1)

    assert signal.comment == "ATR_impulse"


def _run_strategy_to_bar(
    strategy: XauAtrImpulseStrategy | XauQuickMomentumStrategy,
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


def _atr_impulse_long_bars(
    *,
    overbought: bool = False,
    breakout: bool = True,
) -> list[dict]:
    if overbought:
        closes = [2000.0 + idx * 0.1 for idx in range(59)]
    else:
        closes = _alternating_closes()

    m5_bars = []
    final_close = closes[-1] + 1.0
    for idx, close in enumerate(closes):
        if idx == len(closes) - 1:
            previous_high = close + 0.1 if breakout else final_close
            m5_bars.append(_bar(idx, close=close, high=previous_high))
        else:
            m5_bars.append(_bar(idx, close=close))
    m5_bars.append(
        _bar(
            len(closes),
            open_=closes[-1],
            close=final_close,
            high=final_close + 0.1 if breakout else final_close,
            low=final_close - 0.1,
        )
    )
    return _m1_bars_from_m5(m5_bars)


def _atr_impulse_short_bars(*, oversold: bool = False) -> list[dict]:
    if oversold:
        closes = [2000.0 - idx * 0.1 for idx in range(59)]
    else:
        closes = _alternating_closes()

    m5_bars = []
    final_close = closes[-1] - 1.0
    for idx, close in enumerate(closes):
        if idx == len(closes) - 1:
            m5_bars.append(_bar(idx, close=close, low=close - 0.1))
        else:
            m5_bars.append(_bar(idx, close=close))
    m5_bars.append(
        _bar(
            len(closes),
            open_=closes[-1],
            close=final_close,
            high=final_close + 0.1,
            low=final_close - 0.1,
        )
    )
    return _m1_bars_from_m5(m5_bars)


def _alternating_closes() -> list[float]:
    return [2000.0 + (0.4 if idx % 2 else -0.4) for idx in range(59)]


def _expected_distances(feature: dict) -> tuple[float, float]:
    sl_distance = max(
        feature["atr"] * 1.2,
        max(feature["current"] * 0.00045, 2.0),
    )
    tp_distance = sl_distance * 2.4
    return sl_distance, tp_distance


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


def _m1_bars_from_m5(m5_bars: list[dict]) -> list[dict]:
    bars: list[dict] = []
    for m5_idx, m5_bar in enumerate(m5_bars):
        start = int(m5_bar["time"])
        neutral = float(m5_bar["open"])
        for minute in range(5):
            is_close_minute = minute == 4
            close = float(m5_bar["close"]) if is_close_minute else neutral
            high = float(m5_bar["high"]) if is_close_minute else max(neutral, close)
            low = float(m5_bar["low"]) if is_close_minute else min(neutral, close)
            bars.append(
                {
                    "time": start + minute * 60,
                    "open": neutral,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1.0,
                }
            )
    return bars


def _bar(
    idx: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
):
    open_value = close if open_ is None else open_
    return {
        "time": 1_700_000_100 + idx * 300,
        "open": open_value,
        "high": max(close + 0.1, 2001.0) if high is None else high,
        "low": min(close - 0.1, 1999.0) if low is None else low,
        "close": close,
        "volume": 1.0,
    }
