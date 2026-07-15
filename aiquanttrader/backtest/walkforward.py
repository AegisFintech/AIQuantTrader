"""Purged walk-forward validation helpers for time-ordered bar series.

Purged k-fold walk-forward on a contiguous time-ordered bar series. Purge =
drop ``n_purge_bars`` between train and test to prevent label/feature leakage.
Embargo = drop ``n_embargo_bars`` after test to prevent future-bar-feature
leakage into the next fold's train.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev
from typing import Callable

from aiquanttrader.backtest.engine import BacktestConfig, Backtester, BacktestResult
from aiquanttrader.backtest.metrics import MetricsReport, compute_metrics
from aiquanttrader.backtest.reporter import Verdict, verdict_for
from aiquanttrader.backtest.strategies.base import Strategy

LOGGER = logging.getLogger(__name__)

_AGGREGATED_METRIC_NAMES = (
    "total_pnl",
    "profit_factor",
    "win_rate",
    "max_drawdown_pct",
    "sharpe_ratio",
    "n_trades",
    "expectancy",
)


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration for purged walk-forward validation."""

    n_folds: int = 5
    n_purge_bars: int = 0
    n_embargo_bars: int = 0
    train_size_bars: int | None = None
    min_train_bars: int = 1000
    min_test_bars: int = 100


@dataclass(frozen=True)
class FoldSplit:
    """One train/test split with explicit purged and embargoed gaps.

    ``train_end`` and ``test_end`` are exclusive epoch boundaries. ``train_bars``
    and ``test_bars`` are copies of the source bar dictionaries.
    """

    fold_idx: int
    train_bars: list[dict]
    test_bars: list[dict]
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    purge_bars: int
    embargo_bars: int


@dataclass(frozen=True)
class FoldResult:
    """Backtest result and metrics for one walk-forward fold."""

    fold: FoldSplit
    result: BacktestResult
    metrics: MetricsReport
    verdict: Verdict


@dataclass(frozen=True)
class MetricAggregate:
    """Aggregate statistics for one metric across folds."""

    mean: float
    std: float
    min: float
    max: float
    worst_fold_idx: int
    best_fold_idx: int


@dataclass(frozen=True)
class AggregatedMetrics:
    """Per-metric aggregate statistics across all walk-forward folds."""

    total_pnl: MetricAggregate
    profit_factor: MetricAggregate
    win_rate: MetricAggregate
    max_drawdown_pct: MetricAggregate
    sharpe_ratio: MetricAggregate
    n_trades: MetricAggregate
    expectancy: MetricAggregate


@dataclass(frozen=True)
class WalkForwardStability:
    """Fold-to-fold stability summary."""

    consistency_score: float
    worst_fold_pnl: float
    best_fold_pnl: float
    fold_pnl_std: float
    interpretation: str


@dataclass(frozen=True)
class WalkForwardResult:
    """Complete walk-forward validation payload."""

    config: WalkForwardConfig
    folds: list[FoldResult]
    aggregated_metrics: AggregatedMetrics
    walk_forward_stability: WalkForwardStability
    verdict: Verdict


def build_folds(bars: list[dict], config: WalkForwardConfig) -> list[FoldSplit]:
    """Build purged walk-forward train/test folds from ordered bars.

    When ``train_size_bars`` is unset, the data is partitioned into an initial
    train segment plus ``n_folds`` contiguous forward test segments. Each later
    train window expands to all bars before that fold's purged test boundary.
    When ``train_size_bars`` is set, a fixed-size rolling train window advances
    by one test segment per fold.
    """

    _validate_config(config)
    ordered_bars = list(bars)
    if not ordered_bars:
        raise ValueError("walk-forward requires at least one bar")

    times = [_bar_time(bar) for bar in ordered_bars]
    if times != sorted(times):
        raise ValueError("walk-forward bars must be sorted by ascending time")

    n_bars = len(ordered_bars)
    interval = _infer_interval_seconds(times)
    if config.train_size_bars is None:
        folds = _expanding_folds(
            bars=ordered_bars,
            times=times,
            interval=interval,
            config=config,
        )
    else:
        folds = _fixed_train_folds(
            bars=ordered_bars,
            times=times,
            interval=interval,
            config=config,
        )

    if not folds:
        raise ValueError(
            "walk-forward produced no folds; increase bars or reduce train/test limits"
        )

    for split in folds:
        if len(split.train_bars) < config.min_train_bars:
            raise ValueError(
                f"fold {split.fold_idx} train bars {len(split.train_bars)} "
                f"is below min_train_bars={config.min_train_bars}"
            )
        if len(split.test_bars) < config.min_test_bars:
            raise ValueError(
                f"fold {split.fold_idx} test bars {len(split.test_bars)} "
                f"is below min_test_bars={config.min_test_bars}"
            )

    if len(folds) < config.n_folds:
        LOGGER.warning(
            "walk-forward produced %s folds instead of requested %s from %s bars",
            len(folds),
            config.n_folds,
            n_bars,
        )
    return folds


def run_walkforward(
    bars: list[dict],
    *,
    strategy_factory: Callable[[], Strategy],
    config: WalkForwardConfig,
    backtest_config: BacktestConfig,
) -> WalkForwardResult:
    """Run a fresh strategy instance over each purged walk-forward test fold."""

    splits = build_folds(bars, config)
    fold_results: list[FoldResult] = []
    for split in splits:
        strategy = strategy_factory()
        result = Backtester(backtest_config).run(
            strategy=strategy,
            bars=split.test_bars,
        )
        metrics = compute_metrics(result)
        fold_results.append(
            FoldResult(
                fold=split,
                result=result,
                metrics=metrics,
                verdict=verdict_for(metrics, []),
            )
        )

    aggregated = aggregate_fold_metrics(fold_results)
    stability = compute_stability(fold_results)
    verdict = _walk_forward_verdict(aggregated=aggregated, stability=stability)
    return WalkForwardResult(
        config=config,
        folds=fold_results,
        aggregated_metrics=aggregated,
        walk_forward_stability=stability,
        verdict=verdict,
    )


def aggregate_fold_metrics(folds: list[FoldResult]) -> AggregatedMetrics:
    """Aggregate standard fold metrics into mean/std/min/max summaries."""

    if not folds:
        raise ValueError("cannot aggregate zero walk-forward folds")
    values_by_metric = {
        metric_name: [
            float(getattr(fold.metrics, metric_name))
            for fold in folds
        ]
        for metric_name in _AGGREGATED_METRIC_NAMES
    }
    return AggregatedMetrics(
        total_pnl=_aggregate_metric(
            values_by_metric["total_pnl"],
            folds=folds,
            higher_is_worse=False,
        ),
        profit_factor=_aggregate_metric(
            values_by_metric["profit_factor"],
            folds=folds,
            higher_is_worse=False,
        ),
        win_rate=_aggregate_metric(
            values_by_metric["win_rate"],
            folds=folds,
            higher_is_worse=False,
        ),
        max_drawdown_pct=_aggregate_metric(
            values_by_metric["max_drawdown_pct"],
            folds=folds,
            higher_is_worse=True,
        ),
        sharpe_ratio=_aggregate_metric(
            values_by_metric["sharpe_ratio"],
            folds=folds,
            higher_is_worse=False,
        ),
        n_trades=_aggregate_metric(
            values_by_metric["n_trades"],
            folds=folds,
            higher_is_worse=False,
        ),
        expectancy=_aggregate_metric(
            values_by_metric["expectancy"],
            folds=folds,
            higher_is_worse=False,
        ),
    )


def compute_stability(folds: list[FoldResult]) -> WalkForwardStability:
    """Compute fold-PnL consistency and variance."""

    if not folds:
        raise ValueError("cannot compute stability for zero walk-forward folds")
    pnls = [float(fold.metrics.total_pnl) for fold in folds]
    positive = sum(1 for pnl in pnls if pnl > 0)
    consistency = positive / len(pnls)
    pnl_std = _std(pnls)
    mean_abs = abs(_mean(pnls))
    high_variance = pnl_std > max(mean_abs, 1.0)
    variance_text = "high variance" if high_variance else "low variance"
    return WalkForwardStability(
        consistency_score=consistency,
        worst_fold_pnl=min(pnls),
        best_fold_pnl=max(pnls),
        fold_pnl_std=pnl_std,
        interpretation=f"{positive}/{len(pnls)} positive, {variance_text}",
    )


def _expanding_folds(
    *,
    bars: list[dict],
    times: list[int],
    interval: int,
    config: WalkForwardConfig,
) -> list[FoldSplit]:
    n_bars = len(bars)
    segment_size = max(1, n_bars // (config.n_folds + 1))
    initial_train_size = segment_size
    embargo_budget = config.n_embargo_bars * max(0, config.n_folds - 1)
    remaining_for_tests = max(0, n_bars - initial_train_size - embargo_budget)
    test_size = max(1, remaining_for_tests // config.n_folds)
    folds: list[FoldSplit] = []
    previous_test_end_idx = 0
    for fold_idx in range(config.n_folds):
        nominal_test_start_idx = initial_train_size + fold_idx * test_size
        test_start_idx = max(
            nominal_test_start_idx,
            previous_test_end_idx + config.n_embargo_bars if fold_idx > 0 else 0,
        )
        split = _make_split(
            fold_idx=fold_idx,
            bars=bars,
            times=times,
            interval=interval,
            train_start_idx=0,
            train_end_idx=max(0, test_start_idx - config.n_purge_bars),
            test_start_idx=test_start_idx,
            test_end_idx=min(n_bars, test_start_idx + test_size),
            config=config,
        )
        folds.append(split)
        previous_test_end_idx = _index_for_exclusive_time(
            times=times,
            boundary=split.test_end,
        )
        if previous_test_end_idx >= n_bars:
            break
    return folds


def _fixed_train_folds(
    *,
    bars: list[dict],
    times: list[int],
    interval: int,
    config: WalkForwardConfig,
) -> list[FoldSplit]:
    n_bars = len(bars)
    train_size = int(config.train_size_bars or 0)
    if train_size <= 0:
        raise ValueError("train_size_bars must be positive when provided")
    if train_size >= n_bars:
        raise ValueError("train_size_bars must be smaller than the bar count")

    test_size = max(1, n_bars // config.n_folds)
    folds: list[FoldSplit] = []
    train_start_idx = 0
    fold_idx = 0
    while fold_idx < config.n_folds and train_start_idx + train_size < n_bars:
        nominal_test_start_idx = train_start_idx + train_size
        if folds:
            previous_test_end_idx = _index_for_exclusive_time(
                times=times,
                boundary=folds[-1].test_end,
            )
            nominal_test_start_idx = max(
                nominal_test_start_idx,
                previous_test_end_idx + config.n_embargo_bars,
            )
            train_start_idx = max(0, nominal_test_start_idx - train_size)

        test_start_idx = nominal_test_start_idx
        split = _make_split(
            fold_idx=fold_idx,
            bars=bars,
            times=times,
            interval=interval,
            train_start_idx=train_start_idx,
            train_end_idx=max(train_start_idx, test_start_idx - config.n_purge_bars),
            test_start_idx=test_start_idx,
            test_end_idx=min(n_bars, test_start_idx + test_size),
            config=config,
        )
        folds.append(split)
        fold_idx += 1
        train_start_idx += test_size
    return folds


def _make_split(
    *,
    fold_idx: int,
    bars: list[dict],
    times: list[int],
    interval: int,
    train_start_idx: int,
    train_end_idx: int,
    test_start_idx: int,
    test_end_idx: int,
    config: WalkForwardConfig,
) -> FoldSplit:
    n_bars = len(bars)
    train_start_idx = _clamp(train_start_idx, 0, n_bars)
    train_end_idx = _clamp(train_end_idx, train_start_idx, n_bars)
    test_start_idx = _clamp(test_start_idx, train_end_idx, n_bars)
    test_end_idx = _clamp(test_end_idx, test_start_idx, n_bars)
    actual_purge = max(0, test_start_idx - train_end_idx)
    actual_embargo = min(config.n_embargo_bars, max(0, n_bars - test_end_idx))
    if actual_purge < config.n_purge_bars:
        LOGGER.warning(
            "fold %s applied purge_bars=%s below requested %s",
            fold_idx,
            actual_purge,
            config.n_purge_bars,
        )
    if actual_embargo < config.n_embargo_bars:
        LOGGER.warning(
            "fold %s applied embargo_bars=%s below requested %s",
            fold_idx,
            actual_embargo,
            config.n_embargo_bars,
        )

    return FoldSplit(
        fold_idx=fold_idx,
        train_bars=[dict(bar) for bar in bars[train_start_idx:train_end_idx]],
        test_bars=[dict(bar) for bar in bars[test_start_idx:test_end_idx]],
        train_start=_boundary_time(times, train_start_idx, interval),
        train_end=_boundary_time(times, train_end_idx, interval),
        test_start=_boundary_time(times, test_start_idx, interval),
        test_end=_boundary_time(times, test_end_idx, interval),
        purge_bars=actual_purge,
        embargo_bars=actual_embargo,
    )


def _walk_forward_verdict(
    *,
    aggregated: AggregatedMetrics,
    stability: WalkForwardStability,
) -> Verdict:
    mean_pnl = aggregated.total_pnl.mean
    mean_profit_factor = aggregated.profit_factor.mean
    mean_drawdown_pct = aggregated.max_drawdown_pct.mean
    catastrophic = stability.worst_fold_pnl < -2.0 * abs(mean_pnl)
    pass_checks = [
        ("consistency_score >= 0.8", stability.consistency_score >= 0.8),
        ("mean profit_factor >= 1.5", mean_profit_factor >= 1.5),
        ("worst_fold_pnl > 0", stability.worst_fold_pnl > 0),
        ("mean max_drawdown_pct <= 0.20", mean_drawdown_pct <= 0.20),
    ]
    fail_checks = [
        ("consistency_score < 0.5", stability.consistency_score < 0.5),
        ("worst_fold_pnl < -2 * abs(mean_pnl)", catastrophic),
        ("mean profit_factor < 0.8", mean_profit_factor < 0.8),
    ]

    if all(passed for _, passed in pass_checks):
        status = "pass"
        summary = "Summary: PASS because all walk-forward promotion gates passed."
    elif any(failed for _, failed in fail_checks):
        status = "fail"
        summary = "Summary: FAIL because at least one walk-forward rejection gate fired."
    else:
        status = "marginal"
        summary = "Summary: MARGINAL because the run avoided hard failure but missed promotion gates."

    worst_idx = aggregated.total_pnl.worst_fold_idx
    best_idx = aggregated.total_pnl.best_fold_idx
    lines = [summary, "", "Walk-forward promotion gates:"]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} `{rule}`"
        for rule, passed in pass_checks
    )
    lines.append("")
    lines.append("Walk-forward rejection gates:")
    lines.extend(
        f"- {'FAIL' if failed else 'PASS'} `{rule}`"
        for rule, failed in fail_checks
    )
    lines.extend(
        [
            "",
            f"Worst fold index: {worst_idx}",
            f"Best fold index: {best_idx}",
            f"Best fold PnL: {_format_float(stability.best_fold_pnl)}",
            f"Worst fold PnL: {_format_float(stability.worst_fold_pnl)}",
        ]
    )
    return Verdict(status=status, rationale="\n".join(lines))


def _aggregate_metric(
    values: list[float],
    *,
    folds: list[FoldResult],
    higher_is_worse: bool,
) -> MetricAggregate:
    worst_value = max(values) if higher_is_worse else min(values)
    best_value = min(values) if higher_is_worse else max(values)
    worst_position = values.index(worst_value)
    best_position = values.index(best_value)
    return MetricAggregate(
        mean=_mean(values),
        std=_std(values),
        min=min(values),
        max=max(values),
        worst_fold_idx=folds[worst_position].fold.fold_idx,
        best_fold_idx=folds[best_position].fold.fold_idx,
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    if all(math.isfinite(value) for value in values):
        return mean(values)
    if any(math.isinf(value) and value > 0 for value in values):
        return math.inf
    if any(math.isinf(value) and value < 0 for value in values):
        return -math.inf
    return math.nan


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    if all(math.isfinite(value) for value in values):
        return pstdev(values)
    return 0.0 if all(value == values[0] for value in values) else math.inf


def _validate_config(config: WalkForwardConfig) -> None:
    if config.n_folds < 1:
        raise ValueError("n_folds must be at least 1")
    if config.n_purge_bars < 0:
        raise ValueError("n_purge_bars cannot be negative")
    if config.n_embargo_bars < 0:
        raise ValueError("n_embargo_bars cannot be negative")
    if config.min_train_bars < 0:
        raise ValueError("min_train_bars cannot be negative")
    if config.min_test_bars < 1:
        raise ValueError("min_test_bars must be at least 1")


def _bar_time(bar: dict) -> int:
    value = bar.get("time", bar.get("ts", bar.get("ts_server")))
    if value is None or value == "":
        raise ValueError("bar time is required")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    raise ValueError(f"unsupported bar time: {value!r}")


def _infer_interval_seconds(times: list[int]) -> int:
    if len(times) < 2:
        return 60
    intervals = [
        current - previous
        for previous, current in zip(times, times[1:])
        if current > previous
    ]
    return min(intervals) if intervals else 60


def _boundary_time(times: list[int], index: int, interval: int) -> int:
    if index < len(times):
        return int(times[index])
    return int(times[-1] + interval)


def _index_for_exclusive_time(*, times: list[int], boundary: int) -> int:
    for index, value in enumerate(times):
        if value >= boundary:
            return index
    return len(times)


def _clamp(value: int, lower: int, upper: int) -> int:
    return min(max(int(value), int(lower)), int(upper))


def _format_float(value: float) -> str:
    number = float(value)
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    if math.isnan(number):
        return "nan"
    return f"{number:.6g}"
