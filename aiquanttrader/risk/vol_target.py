"""Volatility targeting for position sizing.

Scales position sizes inversely to realized volatility so that
portfolio risk contribution remains constant regardless of market regime.
"""

from __future__ import annotations

import numpy as np


def volatility_target_scalar(
    recent_returns: np.ndarray,
    *,
    target_annual_vol: float = 0.12,
    periods_per_year: int = 69552,
    halflife: int = 20,
    min_scalar: float = 0.1,
    max_scalar: float = 3.0,
) -> float:
    """Compute position size scalar based on volatility targeting.

    Args:
        recent_returns: Array of recent bar-to-bar returns
        target_annual_vol: Target annualized portfolio volatility (default 12%)
        periods_per_year: Number of bars per year (M5 XAUUSD = 69552)
        halflife: Exponential decay halflife for vol estimation
        min_scalar: Floor for the scalar (never go below 10% of base size)
        max_scalar: Cap for the scalar (never exceed 3x base size)

    Returns:
        Scalar to multiply base position size by.
        >1 means increase size (low vol), <1 means decrease (high vol).
    """
    if len(recent_returns) < 10:
        return 1.0

    # Exponentially weighted realized volatility
    n = len(recent_returns)
    decay = np.exp(-np.log(2) * np.arange(n)[::-1] / halflife)
    weights = decay / decay.sum()

    weighted_var = float(np.sum(weights * (recent_returns - np.mean(recent_returns)) ** 2))
    realized_vol = np.sqrt(weighted_var * periods_per_year)

    if realized_vol == 0:
        return 1.0

    scalar = target_annual_vol / realized_vol
    return float(np.clip(scalar, min_scalar, max_scalar))


def realized_volatility(
    returns: np.ndarray,
    *,
    periods_per_year: int = 69552,
    halflife: int = 20,
) -> float:
    """Compute annualized exponentially-weighted realized volatility."""
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    decay = np.exp(-np.log(2) * np.arange(n)[::-1] / halflife)
    weights = decay / decay.sum()

    weighted_var = float(np.sum(weights * (returns - np.mean(returns)) ** 2))
    return float(np.sqrt(weighted_var * periods_per_year))
