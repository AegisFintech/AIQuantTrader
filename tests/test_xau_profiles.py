from __future__ import annotations

from finrobot.xau_profiles import XauStrategyProfile, read_profile_csv, write_profile_csv


def test_profile_csv_round_trip_and_bounds(tmp_path):
    path = tmp_path / "finrobot_strategy_profile.csv"
    profile = XauStrategyProfile(
        profile_name="too_hot",
        risk_tier=9,
        daily_risk_per_trade_fraction=0.99,
        daily_loss_limit_fraction=0.99,
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
    assert rows["daily_risk_per_trade_fraction"] == "0.005"
    assert rows["daily_loss_limit_fraction"] == "0.05"
    assert rows["max_auto_positions_xauusd"] == "4"
    assert rows["min_seconds_between_trades_xauusd"] == "30"
    assert rows["loss_streak_pause_count"] == "8"
    assert rows["bad_day_downshift_fraction"] == "0"
    assert rows["max_recent_drawdown_fraction"] == "0.05"
    assert rows["max_atr_regime_multiplier"] == "8"
