import pandas as pd

from aiquanttrader.backtesting import BacktestConfig, backtest_trend_martingale, build_trend_signals_from_m1


def _sample_m1(rows: int = 300):
    idx = pd.date_range("2025-01-01", periods=rows, freq="min", tz="UTC")
    close = pd.Series(range(rows), dtype=float) + 100.0
    return pd.DataFrame(
        {
            "time": idx,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "tick_volume": 1,
        }
    )


def test_build_trend_signals_has_signal_column():
    m1 = _sample_m1()
    out = build_trend_signals_from_m1(m1)
    assert "signal" in out.columns


def test_backtest_trend_martingale_returns_stats():
    m1 = _sample_m1()
    stats = backtest_trend_martingale(m1, BacktestConfig())
    assert "total_return" in stats
    assert "max_drawdown" in stats
    assert "num_trades" in stats
