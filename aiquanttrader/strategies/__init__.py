"""
Trading Strategies Module

Contains all trading strategy implementations:
- Grid Trading
- Martingale Trend Following
- High Frequency Trading (HFT)
- Harmonic Patterns
- Smart Money Concepts
"""

from aiquanttrader.strategies.grid import GridConfig, backtest_xauusd_grid
from aiquanttrader.strategies.backtesting import BacktestConfig, backtest_trend_martingale
from aiquanttrader.hft import HFTConfig, backtest_hft

__all__ = [
    # Grid
    "GridConfig",
    "backtest_xauusd_grid",
    # Martingale
    "BacktestConfig",
    "backtest_trend_martingale",
    # HFT
    "HFTConfig",
    "backtest_hft",
]
