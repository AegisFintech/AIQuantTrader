"""Public backtesting surface for Phase 3 research workflows.

The package exposes a deterministic bar-by-bar engine, pure fill and sizing
helpers, standard backtest metrics, a strategy interface, the M2.1
BuyAndHold smoke strategy, and the M2.2 parity harness skeleton.
"""

from __future__ import annotations

from aiquanttrader.backtest.engine import (
    Backtester,
    BacktestConfig,
    BacktestResult,
    BreakEvenConfig,
)
from aiquanttrader.backtest.fills import FillConfig, FillModel, simulate_fill
from aiquanttrader.backtest.instruments import InstrumentSpec, XAUUSD_ICMARKETS_DEMO
from aiquanttrader.backtest.metrics import (
    MetricsReport,
    compute_metrics,
    expectancy,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from aiquanttrader.backtest.parity import ParityReport, compare_decisions
from aiquanttrader.backtest.position import (
    DailyRiskSizer,
    Position,
    PositionSizer,
    PositionState,
)
from aiquanttrader.backtest.reporter import (
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
from aiquanttrader.backtest.strategies.base import Signal, Strategy
from aiquanttrader.backtest.strategies.buy_and_hold import BuyAndHold
from aiquanttrader.backtest.strategies.xau_atr_impulse import (
    XauAtrImpulseParams,
    XauAtrImpulseStrategy,
)
from aiquanttrader.backtest.strategies.xau_gates import (
    XauGateParams,
    pda,
    smc_long_score,
    smc_short_score,
)
from aiquanttrader.backtest.strategies.xau_gated import XauGatedParams, XauGatedStrategy
from aiquanttrader.backtest.strategies.xau_quick_momentum import (
    XauQuickMomentumParams,
    XauQuickMomentumStrategy,
)
from aiquanttrader.backtest.walkforward import (
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
    "InstrumentSpec",
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
    "XAUUSD_ICMARKETS_DEMO",
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
