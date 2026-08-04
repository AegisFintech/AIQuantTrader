"""Deterministic uncertainty and multiple-selection penalties."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    confidence: float
    block_size: int
    resamples: int
    seed: int


@dataclass(frozen=True, slots=True)
class SelectionBiasReport:
    observed_mean: float
    standard_error: float
    candidate_count: int
    family_wise_alpha: float
    critical_z: float
    deflated_lower_bound: float


def moving_block_bootstrap_mean(
    values: tuple[float, ...],
    *,
    block_size: int,
    resamples: int = 2_000,
    confidence: float = 0.95,
    seed: int = 0,
) -> BootstrapInterval:
    if len(values) < 2:
        raise ValueError("bootstrap requires at least two observations")
    if not 1 <= block_size <= len(values):
        raise ValueError("block size must be within the sample")
    if resamples < 100:
        raise ValueError("bootstrap requires at least 100 resamples")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    sample = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(sample)):
        raise ValueError("bootstrap values must be finite")
    rng = np.random.default_rng(seed)
    starts = np.arange(len(sample) - block_size + 1)
    blocks_needed = math.ceil(len(sample) / block_size)
    means = np.empty(resamples, dtype=np.float64)
    for index in range(resamples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        draw = np.concatenate([sample[start : start + block_size] for start in chosen])
        means[index] = float(np.mean(draw[: len(sample)]))
    alpha = (1 - confidence) / 2
    lower, upper = np.quantile(means, [alpha, 1 - alpha])
    return BootstrapInterval(
        estimate=float(np.mean(sample)),
        lower=float(lower),
        upper=float(upper),
        confidence=confidence,
        block_size=block_size,
        resamples=resamples,
        seed=seed,
    )


def deflated_selection_mean(
    values: tuple[float, ...], *, candidate_count: int, family_wise_alpha: float = 0.05
) -> SelectionBiasReport:
    """One-sided Bonferroni bound exposing selection-family size explicitly."""

    if len(values) < 2:
        raise ValueError("selection report requires at least two observations")
    if candidate_count < 1:
        raise ValueError("candidate count must be positive")
    if not 0 < family_wise_alpha < 0.5:
        raise ValueError("family-wise alpha must be in (0, 0.5)")
    sample = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(sample)):
        raise ValueError("selection values must be finite")
    mean = float(np.mean(sample))
    standard_error = float(np.std(sample, ddof=1) / math.sqrt(len(sample)))
    critical_z = NormalDist().inv_cdf(1 - family_wise_alpha / candidate_count)
    return SelectionBiasReport(
        observed_mean=mean,
        standard_error=standard_error,
        candidate_count=candidate_count,
        family_wise_alpha=family_wise_alpha,
        critical_z=critical_z,
        deflated_lower_bound=mean - critical_z * standard_error,
    )
