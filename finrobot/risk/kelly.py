"""Kelly criterion position sizing.

Implements full and fractional Kelly for optimal geometric growth rate.
Uses rolling trade statistics with exponential decay weighting.
"""

from __future__ import annotations

import math

import numpy as np


def kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """Compute full Kelly fraction: f* = (b*p - q) / b.

    Where b = avg_win/|avg_loss| (odds), p = win_rate, q = 1-p.
    Returns 0 if edge is non-positive or inputs are invalid.
    """
    if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0

    b = abs(avg_win / avg_loss)
    p = win_rate
    q = 1.0 - p

    f = (b * p - q) / b

    return max(0.0, f)


def fractional_kelly_size(
    equity: float,
    trade_pnls: np.ndarray,
    *,
    fraction: float = 0.25,
    min_trades: int = 30,
    decay_halflife: int = 50,
    max_fraction: float = 0.02,
) -> float:
    """Compute position size as a fraction of equity using fractional Kelly.

    Args:
        equity: Current account equity
        trade_pnls: Array of recent trade P&Ls (most recent last)
        fraction: Kelly fraction (0.25 = quarter-Kelly, conservative)
        min_trades: Minimum trades required before Kelly kicks in
        decay_halflife: Exponential decay halflife for weighting recent trades
        max_fraction: Maximum fraction of equity to risk (hard cap)

    Returns:
        Dollar amount to risk on next trade.
    """
    if len(trade_pnls) < min_trades or equity <= 0:
        return 0.0

    # Apply exponential decay weighting (recent trades matter more)
    n = len(trade_pnls)
    decay = np.exp(-np.log(2) * np.arange(n)[::-1] / decay_halflife)
    weights = decay / decay.sum()

    weighted_pnls = trade_pnls * weights * n

    wins = weighted_pnls[weighted_pnls > 0]
    losses = weighted_pnls[weighted_pnls < 0]

    if len(wins) == 0 or len(losses) == 0:
        return 0.0

    win_rate = len(wins) / len(weighted_pnls)
    avg_win = float(np.mean(wins))
    avg_loss = float(np.mean(losses))

    full_kelly = kelly_fraction(win_rate, avg_win, avg_loss)
    risk_fraction = min(full_kelly * fraction, max_fraction)

    return equity * risk_fraction


def kelly_from_returns(
    returns: np.ndarray,
    *,
    fraction: float = 0.25,
) -> float:
    """Compute fractional Kelly from a return series.

    Optimal fraction for continuous returns: f* = mu / sigma^2.
    Returns the fraction-scaled result capped at 2.0.
    """
    if len(returns) < 10:
        return 0.0

    mu = float(np.mean(returns))
    var = float(np.var(returns, ddof=1))

    if var == 0 or mu <= 0:
        return 0.0

    full_kelly = mu / var
    return min(full_kelly * fraction, 2.0)
