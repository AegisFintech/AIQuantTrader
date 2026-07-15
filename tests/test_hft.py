import pandas as pd

from aiquanttrader.hft import (
    HFTConfig,
    TrendMartingaleConfig,
    backtest_hft,
    next_martingale_lot,
    trend_signal_1m_with_5m_filter,
)


def test_trend_signal_long_and_short():
    m5 = pd.DataFrame({"close": [90 + i for i in range(30)]})

    m1_long = pd.DataFrame({"close": [130, 131, 132]})
    assert trend_signal_1m_with_5m_filter(m1_long, m5) == 1

    m1_short = pd.DataFrame({"close": [80, 79, 78]})
    assert trend_signal_1m_with_5m_filter(m1_short, m5) == -1


def test_next_martingale_lot_caps_at_max_steps():
    cfg = TrendMartingaleConfig(base_lot=0.01, multiplier=2.0, max_steps=3)
    step, lot = next_martingale_lot(10, cfg)
    assert step == 3
    assert lot == 0.08


def _sample_hft_rows():
    idx = pd.date_range("2025-01-01", periods=4, freq="min", tz="UTC")
    close = pd.Series([100.0, 101.0, 101.05, 101.06])
    return pd.DataFrame(
        {
            "time": idx,
            "open": close,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "tick_volume": 10,
        }
    )


def test_backtest_hft_reports_fee_adjusted_returns():
    base_cfg = HFTConfig(
        debug=False,
        fee_bps=0.0,
        tick_threshold=0.0,
        min_price_move_pct=0.0,
        profit_target_ticks=1,
        stop_loss_ticks=100,
        max_hold_bars=10,
        fast_window=2,
        slow_window=3,
    )
    high_fee_cfg = HFTConfig(**{**base_cfg.__dict__, "fee_bps": 100.0})

    no_fee = backtest_hft(_sample_hft_rows(), base_cfg)
    high_fee = backtest_hft(_sample_hft_rows(), high_fee_cfg)

    assert no_fee["num_trades"] == 1
    assert high_fee["num_trades"] == 1
    assert no_fee["total_return"] > high_fee["total_return"]
    assert high_fee["max_drawdown"] < 0
    assert high_fee["trades"][0]["pnl"] < high_fee["trades"][0]["gross_pnl"]
