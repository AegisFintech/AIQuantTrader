"""Graduated drawdown response and regime-conditional risk limits.

Implements a multi-tier drawdown budget that progressively reduces
risk as losses accumulate, preventing catastrophic drawdown paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskAction(Enum):
    NORMAL = "normal"
    REDUCE = "reduce"
    HALT = "halt"
    FLATTEN = "flatten"


@dataclass
class DrawdownBudget:
    """Multi-tier drawdown budget configuration."""

    daily_reduce_pct: float = 0.01    # 1% daily → halve sizes
    daily_halt_pct: float = 0.02      # 2% daily → stop new entries
    monthly_halt_pct: float = 0.05    # 5% monthly → suspend
    annual_flatten_pct: float = 0.08  # 8% annual → flatten all


@dataclass
class RiskState:
    """Current risk state for decision-making."""

    action: RiskAction
    size_scalar: float  # multiplier for position sizes (0.0 to 1.0)
    reason: str


def check_drawdown_limits(
    *,
    daily_pnl_pct: float,
    monthly_pnl_pct: float,
    annual_pnl_pct: float,
    budget: DrawdownBudget | None = None,
) -> RiskState:
    """Check drawdown limits and return the appropriate risk action.

    All PnL percentages are negative for losses (e.g., -0.015 = -1.5%).

    Returns the most restrictive action that applies.
    """
    if budget is None:
        budget = DrawdownBudget()

    # Most severe first
    if annual_pnl_pct <= -budget.annual_flatten_pct:
        return RiskState(
            action=RiskAction.FLATTEN,
            size_scalar=0.0,
            reason=f"Annual drawdown {annual_pnl_pct:.1%} exceeds {budget.annual_flatten_pct:.1%} limit",
        )

    if monthly_pnl_pct <= -budget.monthly_halt_pct:
        return RiskState(
            action=RiskAction.HALT,
            size_scalar=0.0,
            reason=f"Monthly drawdown {monthly_pnl_pct:.1%} exceeds {budget.monthly_halt_pct:.1%} limit",
        )

    if daily_pnl_pct <= -budget.daily_halt_pct:
        return RiskState(
            action=RiskAction.HALT,
            size_scalar=0.0,
            reason=f"Daily drawdown {daily_pnl_pct:.1%} exceeds {budget.daily_halt_pct:.1%} limit",
        )

    if daily_pnl_pct <= -budget.daily_reduce_pct:
        return RiskState(
            action=RiskAction.REDUCE,
            size_scalar=0.5,
            reason=f"Daily drawdown {daily_pnl_pct:.1%} exceeds {budget.daily_reduce_pct:.1%} — sizes halved",
        )

    return RiskState(
        action=RiskAction.NORMAL,
        size_scalar=1.0,
        reason="Within all drawdown limits",
    )


def regime_risk_scalar(regime: str) -> float:
    """Return position size scalar based on current market regime.

    Conservative by default: only full sizing in trending markets.
    """
    scalars = {
        "trending": 1.0,
        "ranging": 0.5,
        "volatile": 0.3,
        "unknown": 0.5,
    }
    return scalars.get(regime, 0.5)
