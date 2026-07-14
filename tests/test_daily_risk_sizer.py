from __future__ import annotations

import pytest

from finrobot.backtest import DailyRiskSizer


def test_daily_risk_sizer_basic():
    sizer = _sizer(max_lot_per_trade=0.10)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=0.0,
    )

    assert volume == 0.10


def test_daily_risk_sizer_high_confluence_multiplier():
    sizer = _sizer(max_lot_per_trade=2.0)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=0.0,
        smc_score=5,
    )

    assert volume == 1.50


def test_daily_risk_sizer_caps_effective_risk_at_one_percent():
    sizer = DailyRiskSizer(
        risk_per_trade_fraction=0.01,
        daily_loss_cap_fraction=0.01,
        max_lot_per_trade=100.0,
        max_positions_per_symbol=2,
        high_confluence_lot_multiplier=3.0,
        high_confluence_score=5,
        max_effective_risk_fraction=0.01,
    )

    volume = sizer.size(
        symbol="XAUUSD",
        equity=1_000_000.0,
        sl_distance=400.0,
        open_positions=[],
        today_closed_pnl=0.0,
        smc_score=5,
    )

    assert volume == 25.0


def test_daily_risk_sizer_below_threshold_no_multiplier():
    sizer = _sizer(max_lot_per_trade=2.0)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=0.0,
        smc_score=4,
    )

    assert volume == 0.50


def test_daily_risk_sizer_per_symbol_max_lot():
    sizer = _sizer(
        max_lot_per_trade=5.0,
        max_lot_per_symbol={"XAUUSD": 5.0},
    )

    volume = sizer.size(
        symbol="XAUUSD",
        equity=1_000_000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=0.0,
        smc_score=5,
    )

    assert volume == 5.0


def test_proportional_sizing_across_account_sizes():
    sizer = _sizer(
        max_lot_per_trade=5.0,
        max_lot_per_symbol={"XAUUSD": 5.0},
    )

    account_sizes = [10_000.0, 100_000.0, 1_000_000.0, 5_000_000.0]
    volumes = [
        sizer.size(
            symbol="XAUUSD",
            equity=equity,
            sl_distance=1000.0,
            open_positions=[],
            today_closed_pnl=0.0,
        )
        for equity in account_sizes
    ]

    assert volumes == [0.01, 0.10, 1.0, 5.0]
    assert volumes[-1] <= 5.0
    assert volumes[1] / volumes[0] == pytest.approx(10.0)
    assert volumes[2] / volumes[1] == pytest.approx(10.0)
    assert volumes[3] / volumes[2] == pytest.approx(5.0)


def test_daily_risk_sizer_rounds_lot_step():
    sizer = _sizer(max_lot_per_trade=1.0)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=30.0,
        open_positions=[],
        today_closed_pnl=0.0,
    )

    assert volume == 0.33


def test_daily_risk_sizer_zero_sl_returns_zero():
    sizer = _sizer(max_lot_per_trade=1.0)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=0.0,
        open_positions=[],
        today_closed_pnl=0.0,
        smc_score=5,
    )

    assert volume == 0.0


def test_daily_risk_sizer_downshifts_after_bad_day():
    sizer = _sizer(max_lot_per_trade=2.0, bad_day_downshift_fraction=0.5)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=-1.0,
    )

    assert volume == 0.25


def test_daily_risk_sizer_can_pause_after_bad_day():
    sizer = _sizer(max_lot_per_trade=2.0, bad_day_downshift_fraction=0.0)

    volume = sizer.size(
        symbol="XAUUSD",
        equity=10000.0,
        sl_distance=20.0,
        open_positions=[],
        today_closed_pnl=-1.0,
    )

    assert volume == 0.0


def _sizer(
    *,
    max_lot_per_trade: float,
    max_lot_per_symbol: dict[str, float] | None = None,
    bad_day_downshift_fraction: float = 1.0,
) -> DailyRiskSizer:
    return DailyRiskSizer(
        risk_per_trade_fraction=0.001,
        daily_loss_cap_fraction=0.01,
        max_lot_per_trade=max_lot_per_trade,
        max_positions_per_symbol=2,
        max_lot_per_symbol=max_lot_per_symbol,
        high_confluence_lot_multiplier=3.0,
        high_confluence_score=5,
        bad_day_downshift_fraction=bad_day_downshift_fraction,
    )
