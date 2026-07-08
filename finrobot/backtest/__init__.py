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
from finrobot.backtest.position import (
    DailyRiskSizer,
    Position,
    PositionSizer,
    PositionState,
)
from finrobot.backtest.reporter import (
    BacktestReport,
    DrawdownWindow,
    ReportMetadata,
    StrategyAttribution,
    Verdict,
    generate_report,
    render_markdown,
    write_json,
    write_markdown,
)
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
from finrobot.backtest.walkforward import (
    AggregatedMetrics,
    FoldResult,
    FoldSplit,
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardStability,
    aggregate_fold_metrics,
    build_folds,
    compute_stability,
    run_walkforward,
)

__all__ = [
    "Backtester",
    "BacktestConfig",
    "BacktestReport",
    "BacktestResult",
    "BreakEvenConfig",
    "BuyAndHold",
    "DailyRiskSizer",
    "DrawdownWindow",
    "FillConfig",
    "FillModel",
    "MetricsReport",
    "ParityReport",
    "Position",
    "PositionSizer",
    "PositionState",
    "ReportMetadata",
    "Signal",
    "Strategy",
    "StrategyAttribution",
    "Verdict",
    "AggregatedMetrics",
    "FoldResult",
    "FoldSplit",
    "WalkForwardConfig",
    "WalkForwardResult",
    "WalkForwardStability",
    "XauAtrImpulseParams",
    "XauAtrImpulseStrategy",
    "XauGateParams",
    "XauGatedParams",
    "XauGatedStrategy",
    "XauQuickMomentumParams",
    "XauQuickMomentumStrategy",
    "aggregate_fold_metrics",
    "compare_decisions",
    "build_folds",
    "compute_metrics",
    "compute_stability",
    "expectancy",
    "generate_report",
    "max_drawdown",
    "pda",
    "profit_factor",
    "render_markdown",
    "run_walkforward",
    "sharpe_ratio",
    "simulate_fill",
    "smc_long_score",
    "smc_short_score",
    "win_rate",
    "write_json",
    "write_markdown",
]
