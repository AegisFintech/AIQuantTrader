from __future__ import annotations

import pytest

from aiquanttrader.backtest.fills import FillConfig, simulate_fill


def test_buy_fill_adds_spread_and_slippage():
    fill_price, slippage, _ = simulate_fill(
        side="BUY",
        intended_price=2000.00,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(spread_points=0.10, slippage_points=0.02),
    )

    assert fill_price > 2000.00
    assert slippage > 0


def test_sell_fill_subtracts_spread_and_slippage():
    fill_price, slippage, _ = simulate_fill(
        side="SELL",
        intended_price=2000.00,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(spread_points=0.10, slippage_points=0.02),
    )

    assert fill_price < 2000.00
    assert slippage > 0


def test_zero_spread_zero_slippage_returns_intended_price():
    fill_price, slippage, commission = simulate_fill(
        side="BUY",
        intended_price=2000.00,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(spread_points=0.0, slippage_points=0.0),
    )

    assert fill_price == pytest.approx(2000.00)
    assert slippage == pytest.approx(0.0)
    assert commission == pytest.approx(0.0)


def test_point_size_converts_spread_points_to_price_units():
    fill_price, slippage, _ = simulate_fill(
        side="BUY",
        intended_price=4000.00,
        bar_high=4001.00,
        bar_low=3999.00,
        config=FillConfig(point_size=0.01, spread_points=5.0),
    )

    assert fill_price == pytest.approx(4000.025)
    assert slippage == pytest.approx(0.025)


def test_point_size_must_be_positive():
    with pytest.raises(ValueError, match="point_size must be positive"):
        simulate_fill(
            side="BUY",
            intended_price=4000.00,
            bar_high=4001.00,
            bar_low=3999.00,
            config=FillConfig(point_size=0.0),
        )


def test_fill_clamps_to_bar_high_when_raw_fill_is_above_high():
    fill_price, slippage, _ = simulate_fill(
        side="BUY",
        intended_price=2000.50,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(spread_points=2.0),
    )

    assert fill_price == pytest.approx(2000.50)
    assert slippage > 0


def test_fill_clamps_to_bar_low_when_raw_fill_is_below_low():
    fill_price, slippage, _ = simulate_fill(
        side="SELL",
        intended_price=1999.50,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(spread_points=2.0),
    )

    assert fill_price == pytest.approx(1999.50)
    assert slippage > 0


def test_fill_returns_commission_per_lot():
    _, _, commission = simulate_fill(
        side="BUY",
        intended_price=2000.00,
        bar_high=2000.50,
        bar_low=1999.50,
        config=FillConfig(commission_per_lot=7.0),
    )

    assert commission == pytest.approx(7.0)


def test_different_fill_configs_produce_different_results():
    base = simulate_fill(
        side="BUY",
        intended_price=2000.00,
        bar_high=2005.00,
        bar_low=1995.00,
        config=FillConfig(spread_points=0.0, slippage_points=0.0),
    )
    wider = simulate_fill(
        side="BUY",
        intended_price=2000.00,
        bar_high=2005.00,
        bar_low=1995.00,
        config=FillConfig(spread_points=2.0, slippage_points=0.5),
    )

    assert base != wider
    assert wider[0] > base[0]


def test_simulate_fill_is_deterministic():
    kwargs = {
        "side": "SELL",
        "intended_price": 2000.00,
        "bar_high": 2005.00,
        "bar_low": 1995.00,
        "config": FillConfig(spread_points=1.0, slippage_points=0.25),
    }

    assert simulate_fill(**kwargs) == simulate_fill(**kwargs)
