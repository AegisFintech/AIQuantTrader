"""Statistical significance testing for strategy validation.

Implements:
- Deflated Sharpe Ratio (DSR) — corrects for multiple testing
- Monte Carlo permutation test — null distribution of entry times
- Minimum backtest length calculator
- Bootstrap confidence intervals
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass
class DSRResult:
    """Deflated Sharpe Ratio test result."""

    observed_sharpe: float
    expected_max_sharpe: float
    deflated_sharpe: float
    p_value: float
    n_trials: int
    is_significant: bool


@dataclass
class PermutationResult:
    """Monte Carlo permutation test result."""

    observed_sharpe: float
    null_mean: float
    null_std: float
    p_value: float
    n_permutations: int
    is_significant: bool


@dataclass
class BootstrapCI:
    """Bootstrap confidence interval for a metric."""

    point_estimate: float
    ci_lower: float
    ci_upper: float
    confidence: float


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_trials: int,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
    significance_level: float = 0.05,
) -> DSRResult:
    """Compute the Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    Accounts for the selection bias when the best strategy is chosen
    from `n_trials` independent experiments.

    Args:
        observed_sharpe: Sharpe ratio of the selected strategy
        n_trials: Number of strategies/experiments tested
        n_observations: Number of return observations
        skewness: Skewness of strategy returns
        kurtosis: Excess kurtosis of strategy returns
        significance_level: Alpha for hypothesis test
    """
    if n_trials < 1 or n_observations < 2:
        return DSRResult(
            observed_sharpe=observed_sharpe,
            expected_max_sharpe=0.0,
            deflated_sharpe=0.0,
            p_value=1.0,
            n_trials=n_trials,
            is_significant=False,
        )

    # Expected maximum Sharpe under null (all strategies have zero true Sharpe)
    # E[max(Z_1, ..., Z_n)] approximation using Euler-Mascheroni
    euler_mascheroni = 0.5772156649
    if n_trials == 1:
        expected_max = 0.0
    else:
        expected_max = (
            (1 - euler_mascheroni) * stats.norm.ppf(1 - 1.0 / n_trials)
            + euler_mascheroni * stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
        )

    # Standard error of Sharpe ratio estimate (accounting for non-normality)
    se_sharpe = math.sqrt(
        (1 + 0.5 * observed_sharpe**2 - skewness * observed_sharpe
         + ((kurtosis) / 4.0) * observed_sharpe**2)
        / (n_observations - 1)
    )

    if se_sharpe == 0:
        return DSRResult(
            observed_sharpe=observed_sharpe,
            expected_max_sharpe=expected_max,
            deflated_sharpe=0.0,
            p_value=1.0,
            n_trials=n_trials,
            is_significant=False,
        )

    # DSR statistic
    dsr = (observed_sharpe - expected_max) / se_sharpe

    # p-value from standard normal
    p_value = 1.0 - stats.norm.cdf(dsr)

    return DSRResult(
        observed_sharpe=observed_sharpe,
        expected_max_sharpe=expected_max,
        deflated_sharpe=dsr,
        p_value=p_value,
        n_trials=n_trials,
        is_significant=p_value < significance_level,
    )


def permutation_test(
    trade_pnls: np.ndarray,
    trade_durations: np.ndarray,
    *,
    n_permutations: int = 10000,
    significance_level: float = 0.05,
    random_state: int = 42,
) -> PermutationResult:
    """Monte Carlo permutation test for strategy Sharpe.

    Shuffles trade entry times (preserving duration and P&L of each trade)
    to generate a null distribution. Tests whether the observed sequence
    of returns is significantly better than random timing.
    """
    rng = np.random.default_rng(random_state)

    if len(trade_pnls) < 5:
        return PermutationResult(
            observed_sharpe=0.0,
            null_mean=0.0,
            null_std=0.0,
            p_value=1.0,
            n_permutations=0,
            is_significant=False,
        )

    observed_sharpe = _sharpe_from_pnls(trade_pnls)

    null_sharpes = np.empty(n_permutations)
    for i in range(n_permutations):
        shuffled = rng.permutation(trade_pnls)
        null_sharpes[i] = _sharpe_from_pnls(shuffled)

    null_mean = float(np.mean(null_sharpes))
    null_std = float(np.std(null_sharpes))
    p_value = float(np.mean(null_sharpes >= observed_sharpe))

    return PermutationResult(
        observed_sharpe=observed_sharpe,
        null_mean=null_mean,
        null_std=null_std,
        p_value=p_value,
        n_permutations=n_permutations,
        is_significant=p_value < significance_level,
    )


def minimum_backtest_length(
    observed_sharpe: float,
    *,
    significance_level: float = 0.05,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
) -> int:
    """Minimum number of observations needed for a Sharpe ratio to be significant.

    Based on Bailey formula: N >= (z_alpha / SR)^2 adjusted for non-normality.
    """
    if observed_sharpe <= 0:
        return int(1e9)  # infinite for non-positive Sharpe

    z_alpha = stats.norm.ppf(1 - significance_level)

    # Adjusted for non-normal returns
    correction = 1 + 0.5 * observed_sharpe**2 - skewness * observed_sharpe + (kurtosis / 4.0) * observed_sharpe**2

    n_min = correction * (z_alpha / observed_sharpe) ** 2 + 1
    return int(math.ceil(n_min))


def bootstrap_metric(
    values: np.ndarray,
    *,
    metric_fn=np.mean,
    n_bootstrap: int = 5000,
    confidence: float = 0.95,
    random_state: int = 42,
) -> BootstrapCI:
    """Compute bootstrap confidence interval for any metric.

    Args:
        values: Array of observations (e.g., trade PnLs)
        metric_fn: Function to compute the statistic (default: mean)
        n_bootstrap: Number of bootstrap samples
        confidence: Confidence level (default 95%)
    """
    rng = np.random.default_rng(random_state)
    n = len(values)

    if n < 2:
        point = float(metric_fn(values)) if n == 1 else 0.0
        return BootstrapCI(
            point_estimate=point,
            ci_lower=point,
            ci_upper=point,
            confidence=confidence,
        )

    point_estimate = float(metric_fn(values))
    boot_stats = np.empty(n_bootstrap)

    for i in range(n_bootstrap):
        sample = rng.choice(values, size=n, replace=True)
        boot_stats[i] = metric_fn(sample)

    alpha = 1 - confidence
    ci_lower = float(np.percentile(boot_stats, 100 * alpha / 2))
    ci_upper = float(np.percentile(boot_stats, 100 * (1 - alpha / 2)))

    return BootstrapCI(
        point_estimate=point_estimate,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        confidence=confidence,
    )


def _sharpe_from_pnls(pnls: np.ndarray) -> float:
    if len(pnls) < 2:
        return 0.0
    mu = np.mean(pnls)
    std = np.std(pnls, ddof=1)
    if std == 0:
        return 0.0
    return float(mu / std)
