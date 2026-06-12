"""Public backtesting surface for Phase 3 research workflows.

The package exposes a deterministic bar-by-bar engine, pure fill and sizing
helpers, standard backtest metrics, a strategy interface, the M2.1
BuyAndHold smoke strategy, and the M2.2 parity harness skeleton.
"""

from __future__ import annotations

from finrobot.backtest.engine import (
    Backtester,
    BacktestConfig,
    BacktestResult,
    BreakEvenConfig,
)
from finrobot.backtest.fills import FillConfig, FillModel, simulate_fill
from finrobot.backtest.metrics import (
    MetricsReport,
    compute_metrics,
    expectancy,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from finrobot.backtest.parity import ParityReport, compare_decisions
from finrobot.backtest.position import Position, PositionSizer, PositionState
from finrobot.backtest.strategies.base import Signal, Strategy
from finrobot.backtest.strategies.buy_and_hold import BuyAndHold
from finrobot.backtest.strategies.xau_atr_impulse import (
    XauAtrImpulseParams,
    XauAtrImpulseStrategy,
)
from finrobot.backtest.strategies.xau_gates import (
    XauGateParams,
    pda,
    smc_long_score,
    smc_short_score,
)
from finrobot.backtest.strategies.xau_gated import XauGatedParams, XauGatedStrategy
from finrobot.backtest.strategies.xau_quick_momentum import (
    XauQuickMomentumParams,
    XauQuickMomentumStrategy,
)

__all__ = [
    "Backtester",
    "BacktestConfig",
    "BacktestResult",
    "BreakEvenConfig",
    "BuyAndHold",
    "FillConfig",
    "FillModel",
    "MetricsReport",
    "ParityReport",
    "Position",
    "PositionSizer",
    "PositionState",
    "Signal",
    "Strategy",
    "XauAtrImpulseParams",
    "XauAtrImpulseStrategy",
    "XauGateParams",
    "XauGatedParams",
    "XauGatedStrategy",
    "XauQuickMomentumParams",
    "XauQuickMomentumStrategy",
    "compare_decisions",
    "compute_metrics",
    "expectancy",
    "max_drawdown",
    "pda",
    "profit_factor",
    "sharpe_ratio",
    "simulate_fill",
    "smc_long_score",
    "smc_short_score",
    "win_rate",
]
