"""Alpha decay detection using rolling metrics and CUSUM.

Detects when a strategy's edge is degrading so it can be suspended
before significant capital is lost.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DecayAlert:
    """Alert when a strategy shows signs of alpha decay."""

    strategy_name: str
    metric: str
    current_value: float
    threshold: float
    consecutive_days: int
    action: str  # "monitor", "reduce", "suspend"


def rolling_sharpe(
    pnls: np.ndarray,
    *,
    window: int = 30,
    annualization: float = 252.0,
) -> np.ndarray:
    """Compute rolling Sharpe ratio over a window of trade PnLs.

    Returns an array of the same length (NaN for insufficient data).
    """
    result = np.full(len(pnls), np.nan)
    for i in range(window, len(pnls) + 1):
        chunk = pnls[i - window:i]
        mu = np.mean(chunk)
        std = np.std(chunk, ddof=1)
        if std > 0:
            result[i - 1] = (mu / std) * np.sqrt(annualization)
        else:
            result[i - 1] = 0.0
    return result


def cusum_change_point(
    values: np.ndarray,
    *,
    threshold: float = 3.0,
    drift: float = 0.5,
) -> list[int]:
    """Detect downward change points using CUSUM.

    Returns indices where cumulative negative deviation exceeds threshold.
    """
    if len(values) < 2:
        return []

    mu = np.mean(values)
    std = np.std(values, ddof=1)
    if std == 0:
        return []

    normalized = (values - mu) / std
    change_points: list[int] = []
    s_neg = 0.0

    for i, val in enumerate(normalized):
        s_neg = min(0.0, s_neg + val + drift)
        if s_neg < -threshold:
            change_points.append(i)
            s_neg = 0.0

    return change_points


def check_alpha_decay(
    strategy_name: str,
    daily_pnls: np.ndarray,
    *,
    sharpe_suspend_threshold: float = -0.5,
    sharpe_reduce_threshold: float = 0.0,
    min_days_below: int = 14,
    rolling_window: int = 30,
) -> DecayAlert | None:
    """Check if a strategy shows alpha decay requiring action.

    Returns None if strategy is healthy, or a DecayAlert with recommended action.
    """
    if len(daily_pnls) < rolling_window:
        return None

    sharpes = rolling_sharpe(daily_pnls, window=rolling_window)
    recent_sharpe = sharpes[-1] if not np.isnan(sharpes[-1]) else 0.0

    # Count consecutive days below threshold
    consecutive_below_suspend = 0
    for val in reversed(sharpes):
        if np.isnan(val):
            break
        if val < sharpe_suspend_threshold:
            consecutive_below_suspend += 1
        else:
            break

    if consecutive_below_suspend >= min_days_below:
        return DecayAlert(
            strategy_name=strategy_name,
            metric="rolling_sharpe_30d",
            current_value=recent_sharpe,
            threshold=sharpe_suspend_threshold,
            consecutive_days=consecutive_below_suspend,
            action="suspend",
        )

    consecutive_below_reduce = 0
    for val in reversed(sharpes):
        if np.isnan(val):
            break
        if val < sharpe_reduce_threshold:
            consecutive_below_reduce += 1
        else:
            break

    if consecutive_below_reduce >= min_days_below:
        return DecayAlert(
            strategy_name=strategy_name,
            metric="rolling_sharpe_30d",
            current_value=recent_sharpe,
            threshold=sharpe_reduce_threshold,
            consecutive_days=consecutive_below_reduce,
            action="reduce",
        )

    # Check for structural break via CUSUM
    if len(daily_pnls) >= 60:
        breaks = cusum_change_point(daily_pnls[-60:])
        if breaks and breaks[-1] > 40:
            return DecayAlert(
                strategy_name=strategy_name,
                metric="cusum_break",
                current_value=float(daily_pnls[-30:].mean()),
                threshold=0.0,
                consecutive_days=len(daily_pnls) - 60 + breaks[-1],
                action="monitor",
            )

    return None
