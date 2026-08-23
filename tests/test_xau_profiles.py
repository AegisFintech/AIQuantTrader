from __future__ import annotations

from aiquanttrader.xau_profiles import (
    DEFAULT_PROFILE,
    XauStrategyProfile,
    read_profile_csv,
    write_profile_csv,
)


def test_profile_csv_round_trip_and_bounds(tmp_path):
    path = tmp_path / "aiquanttrader_strategy_profile.csv"
    profile = XauStrategyProfile(
        profile_name="too_hot",
        risk_tier=9,
        daily_risk_per_trade_fraction=0.99,
        daily_loss_limit_fraction=0.99,
        max_lot_per_trade_xauusd=999.0,
        max_auto_positions_xauusd=99,
        max_same_direction_positions_per_symbol=99,
        min_seconds_between_trades_xauusd=1,
        loss_streak_pause_count=99,
        bad_day_downshift_fraction=-1.0,
        max_recent_drawdown_fraction=0.99,
        max_atr_regime_multiplier=99.0,
        break_even_rr_ratio=99.0,
        break_even_extra_points=999.0,
        min_trend_slope_atr_multiplier=99.0,
        higher_trend_timeframe="H1",
        higher_trend_ema_period=999,
    )

    write_profile_csv(profile, path)
    rows = read_profile_csv(path)

    assert rows["profile_name"] == "too_hot"
    assert rows["risk_tier"] == "2"
    assert rows["daily_risk_per_trade_fraction"] == "0.01"
    assert rows["daily_loss_limit_fraction"] == "0.01"
    assert rows["max_lot_per_trade_xauusd"] == "50"
    assert rows["max_auto_positions_xauusd"] == "2"
    assert rows["max_same_direction_positions_per_symbol"] == "2"
    assert rows["min_seconds_between_trades_xauusd"] == "30"
    assert rows["loss_streak_pause_count"] == "8"
    assert rows["bad_day_downshift_fraction"] == "0"
    assert rows["max_recent_drawdown_fraction"] == "0.05"
    assert rows["max_atr_regime_multiplier"] == "8"
    assert rows["enable_macd_histogram_alignment"] == "false"
    assert rows["break_even_rr_ratio"] == "3"
    assert rows["break_even_extra_points"] == "100"
    assert rows["min_trend_slope_atr_multiplier"] == "0.25"
    assert rows["higher_trend_timeframe"] == "M15"
    assert rows["higher_trend_ema_period"] == "200"


def test_default_profile_uses_m1_and_invalid_timeframe_falls_back_to_m1():
    assert DEFAULT_PROFILE.auto_timeframe == "M1"
    assert (
        XauStrategyProfile(
            profile_name="invalid_timeframe",
            risk_tier=0,
            auto_timeframe="H1",
        ).bounded().auto_timeframe
        == "M1"
    )
