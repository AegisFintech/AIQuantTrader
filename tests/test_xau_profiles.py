from __future__ import annotations

from finrobot.xau_profiles import (
    DEFAULT_PROFILE,
    XauStrategyProfile,
    read_profile_csv,
    write_profile_csv,
)


def test_profile_csv_round_trip_and_bounds(tmp_path):
    path = tmp_path / "finrobot_strategy_profile.csv"
    profile = XauStrategyProfile(
        profile_name="too_hot",
        risk_tier=9,
        daily_risk_per_trade_fraction=0.99,
        daily_loss_limit_fraction=0.99,
        max_lot_per_trade_xauusd=999.0,
        max_auto_positions_xauusd=99,
        min_seconds_between_trades_xauusd=1,
        loss_streak_pause_count=99,
        bad_day_downshift_fraction=-1.0,
        max_recent_drawdown_fraction=0.99,
        max_atr_regime_multiplier=99.0,
    )

    write_profile_csv(profile, path)
    rows = read_profile_csv(path)

    assert rows["profile_name"] == "too_hot"
    assert rows["risk_tier"] == "2"
    assert rows["daily_risk_per_trade_fraction"] == "0.01"
    assert rows["daily_loss_limit_fraction"] == "0.05"
    assert rows["max_lot_per_trade_xauusd"] == "50"
    assert rows["max_auto_positions_xauusd"] == "4"
    assert rows["min_seconds_between_trades_xauusd"] == "30"
    assert rows["loss_streak_pause_count"] == "8"
    assert rows["bad_day_downshift_fraction"] == "0"
    assert rows["max_recent_drawdown_fraction"] == "0.05"
    assert rows["max_atr_regime_multiplier"] == "8"
    assert rows["enable_macd_histogram_alignment"] == "false"


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
