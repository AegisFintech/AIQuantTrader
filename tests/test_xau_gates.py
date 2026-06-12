from __future__ import annotations

import pytest

from finrobot.backtest import XauGateParams, XauQuickMomentumParams, pda
from finrobot.backtest.strategies._xau_state import XauRollingFeatureState
from finrobot.backtest.strategies.xau_gates import (
    smc_long_components,
    smc_long_score,
    smc_short_components,
    smc_short_score,
)


def test_pda_warmup_returns_0_5():
    assert pda([_bar(0, close=100.0)], 48, 100.0) == pytest.approx(0.5)


def test_pda_in_discount():
    bars = [_bar(idx, close=120.0 - idx) for idx in range(20)]

    assert pda(bars, 48, bars[-1]["close"]) <= 0.10


def test_pda_in_premium():
    bars = [_bar(idx, close=100.0 + idx) for idx in range(20)]

    assert pda(bars, 48, bars[-1]["close"]) >= 0.90


def test_pda_in_midline():
    bars = [
        _bar(idx, close=100.0, high=105.0, low=95.0)
        for idx in range(20)
    ]

    assert pda(bars, 48, 100.0) == pytest.approx(0.5)


def test_smc_long_score_discount_no_fvg_no_sweep():
    bars = _flat_range_bars(current_price=103.0)

    score, components = smc_long_score(bars, 1.0, 103.0, XauGateParams())

    assert score == 1
    assert components["discount"] is True
    assert components["deep_discount"] is False
    assert components["has_fvg"] is False
    assert components["reclaimed_order_block"] is False
    assert components["sweep"] is False
    assert components["structure"] is False


def test_smc_long_score_deep_discount_bonus():
    bars = _flat_range_bars(current_price=101.0)

    score, components = smc_long_score(bars, 1.0, 101.0, XauGateParams())

    assert score == 2
    assert components["discount"] is True
    assert components["deep_discount"] is True


def test_smc_short_score_premium_mirror():
    bars = _flat_range_bars(current_price=107.0)

    score, components = smc_short_score(bars, 1.0, 107.0, XauGateParams())

    assert score == 1
    assert components["premium"] is True
    assert components["deep_premium"] is False


def test_smc_fvg_bullish_detected():
    bars = [
        _bar(0, close=99.5, high=100.0, low=99.0),
        _bar(1, close=101.0, high=101.5, low=100.8),
        _bar(2, close=103.2, high=104.0, low=103.0),
        _bar(3, close=101.5, high=102.0, low=101.0),
    ]

    components = smc_long_components(bars, 1.0, 101.5, XauGateParams())

    assert components["has_fvg"] is True


def test_smc_liquidity_sweep_bullish():
    bars = [
        _bar(0, close=99.0, high=101.0, low=98.0),
        _bar(1, close=98.5, high=100.0, low=97.0),
        _bar(2, close=97.5, high=99.0, low=96.0),
        _bar(3, close=98.5, high=99.0, low=97.0),
        _bar(4, close=97.0, high=98.0, low=96.0),
        _bar(5, close=96.2, high=97.5, low=94.8),
        _bar(6, close=96.4, high=97.0, low=96.0),
    ]

    components = smc_long_components(bars, 10.0, 96.4, XauGateParams())

    assert components["sweep"] is True


def test_smc_structure_shift_bullish():
    bars = [
        _bar(0, close=101.0, high=104.0, low=99.0),
        _bar(1, close=102.0, high=104.5, low=100.0),
        _bar(2, close=103.0, high=105.0, low=101.0),
        _bar(3, close=102.0, high=104.0, low=100.0),
        _bar(4, close=101.5, high=104.0, low=99.5),
        _bar(5, close=102.5, high=104.5, low=100.5),
        _bar(6, close=102.0, high=105.0, low=100.0),
        _bar(7, close=106.0, high=106.2, low=104.0),
        _bar(8, close=105.5, high=106.0, low=105.0),
    ]

    components = smc_long_components(bars, 1.0, 105.5, XauGateParams())

    assert components["structure"] is True


def test_smc_warmup_no_score_until_six_bars():
    bars = [
        _bar(0, close=101.0, high=104.0, low=99.0),
        _bar(1, close=102.0, high=104.5, low=100.0),
        _bar(2, close=103.0, high=105.0, low=101.0),
        _bar(3, close=102.0, high=104.0, low=100.0),
        _bar(4, close=101.5, high=104.0, low=99.5),
        _bar(5, close=102.5, high=104.5, low=100.5),
        _bar(6, close=106.0, high=106.2, low=104.0),
        _bar(7, close=105.5, high=106.0, low=105.0),
    ]

    components = smc_long_components(bars, 1.0, 105.5, XauGateParams())

    assert components["structure"] is False


def test_smc_bearish_fvg_detected():
    bars = [
        _bar(0, close=109.0, high=110.0, low=108.0),
        _bar(1, close=107.0, high=107.5, low=106.5),
        _bar(2, close=104.5, high=105.0, low=104.0),
        _bar(3, close=107.0, high=107.5, low=106.5),
    ]

    components = smc_short_components(bars, 1.0, 107.0, XauGateParams())

    assert components["has_fvg"] is True


def test_rolling_state_emits_gate_features_only_when_configured():
    bars = _flat_range_bars(current_price=103.0)
    state_without_gates = XauRollingFeatureState(XauQuickMomentumParams())
    state_with_gates = XauRollingFeatureState(
        XauQuickMomentumParams(),
        gate_params=XauGateParams(),
    )

    plain_feature = {}
    gated_feature = {}
    for idx, bar in enumerate(bars):
        plain_feature = state_without_gates.update(idx, bar)
        gated_feature = state_with_gates.update(idx, bar)

    assert "pda" not in plain_feature
    assert gated_feature["pda"] == pytest.approx(0.3)
    assert gated_feature["smc_long_score"] == 1
    assert gated_feature["smc_short_score"] == 0
    assert gated_feature["smc_long_discount"] is True
    assert gated_feature["smc_short_premium"] is False


def _flat_range_bars(*, current_price: float) -> list[dict]:
    bars = [
        _bar(idx, open_=105.0, close=105.0, high=110.0, low=100.0)
        for idx in range(20)
    ]
    bars.append(
        _bar(
            20,
            open_=current_price,
            close=current_price,
            high=current_price + 0.1,
            low=current_price - 0.1,
        )
    )
    return bars


def _bar(
    idx: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    return {
        "time": 1_700_000_000 + idx * 60,
        "open": close if open_ is None else open_,
        "high": close + 0.1 if high is None else high,
        "low": close - 0.1 if low is None else low,
        "close": close,
        "volume": 1.0,
    }
