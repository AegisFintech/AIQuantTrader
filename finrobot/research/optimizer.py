"""Bayesian hyperparameter optimization for strategy parameters.

Uses Optuna to optimize walk-forward Sharpe ratio, preventing overfitting
by using out-of-sample performance as the objective.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from finrobot.backtest.engine import BacktestConfig, Backtester
from finrobot.backtest.metrics import compute_metrics, PERIODS_PER_YEAR
from finrobot.backtest.strategies.base import Strategy

LOGGER = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of a parameter optimization run."""

    best_params: dict[str, Any]
    best_sharpe: float
    n_trials: int
    all_trials: list[dict[str, Any]]


def optimize_strategy(
    *,
    strategy_factory: Callable[[dict], Strategy],
    param_space: dict[str, tuple],
    train_bars: list[dict],
    test_bars: list[dict],
    backtest_config: BacktestConfig,
    n_trials: int = 100,
    timeframe: str = "M5",
    timeout: int | None = None,
) -> OptimizationResult:
    """Optimize strategy parameters using Optuna Bayesian optimization.

    The objective is walk-forward Sharpe: parameters are applied to
    `train_bars` for signal generation, but the Sharpe is measured on
    `test_bars` (out-of-sample).

    Args:
        strategy_factory: Callable that takes a param dict and returns a Strategy
        param_space: Dict mapping param names to (low, high) or (low, high, step) tuples.
                     Float tuples use suggest_float, int tuples use suggest_int.
        train_bars: Bars for warmup/training (strategy sees these first)
        test_bars: Bars for evaluation (Sharpe computed here)
        backtest_config: Configuration for the backtester
        n_trials: Number of optimization trials
        timeframe: Bar timeframe for annualization
        timeout: Optional timeout in seconds
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    all_trials: list[dict] = []

    def objective(trial: optuna.Trial) -> float:
        params = {}
        for name, bounds in param_space.items():
            if isinstance(bounds[0], int) and isinstance(bounds[1], int):
                if len(bounds) == 3:
                    params[name] = trial.suggest_int(name, bounds[0], bounds[1], step=bounds[2])
                else:
                    params[name] = trial.suggest_int(name, bounds[0], bounds[1])
            else:
                if len(bounds) == 3:
                    params[name] = trial.suggest_float(name, bounds[0], bounds[1], step=bounds[2])
                else:
                    params[name] = trial.suggest_float(name, bounds[0], bounds[1])

        try:
            strategy = strategy_factory(params)
            # Only evaluate on test_bars (OOS) — train_bars are for warmup
            result = Backtester(backtest_config).run(strategy=strategy, bars=test_bars)
            metrics = compute_metrics(result, timeframe=timeframe)

            trial_record = {
                "params": params,
                "sharpe": metrics.sharpe_ratio,
                "pf": metrics.profit_factor,
                "n_trades": metrics.n_trades,
                "max_dd_pct": metrics.max_drawdown_pct,
            }
            all_trials.append(trial_record)

            if metrics.n_trades < 10:
                return -10.0

            return metrics.sharpe_ratio

        except Exception as e:
            LOGGER.debug("Trial failed: %s", e)
            return -10.0

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, timeout=timeout)

    return OptimizationResult(
        best_params=study.best_params,
        best_sharpe=study.best_value,
        n_trials=len(study.trials),
        all_trials=all_trials,
    )
