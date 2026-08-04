"""Deterministic feature drift reporting with bounded-cardinality output."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from aiquanttrader_native.features.models import FeatureSchema
from aiquanttrader_native.research.models import DriftReport, FeatureDrift


def calculate_drift(
    baseline: NDArray[np.float64],
    current: NDArray[np.float64],
    *,
    feature_schema: FeatureSchema,
    bins: int = 10,
    psi_threshold: float = 0.2,
    mean_shift_threshold: float = 1.0,
) -> DriftReport:
    expected_columns = len(feature_schema.features)
    for name, values in (("baseline", baseline), ("current", current)):
        if values.ndim != 2 or values.shape[1] != expected_columns or not len(values):
            raise ValueError(f"{name} drift matrix has invalid shape")
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name} drift matrix contains non-finite values")
    if bins < 2 or psi_threshold <= 0 or mean_shift_threshold <= 0:
        raise ValueError("drift bins and thresholds must be positive")

    reports: list[FeatureDrift] = []
    for index, definition in enumerate(feature_schema.features):
        reference = baseline[:, index].astype(np.float64, copy=False)
        observed = current[:, index].astype(np.float64, copy=False)
        quantiles = np.quantile(reference, np.linspace(0, 1, bins + 1)[1:-1])
        interior = np.unique(quantiles)
        edges = np.concatenate(([-np.inf], interior, [np.inf]))
        reference_counts = np.histogram(reference, bins=edges)[0].astype(np.float64)
        observed_counts = np.histogram(observed, bins=edges)[0].astype(np.float64)
        epsilon = 1e-9
        reference_share = np.maximum(reference_counts / len(reference), epsilon)
        observed_share = np.maximum(observed_counts / len(observed), epsilon)
        psi = float(
            np.sum((observed_share - reference_share) * np.log(observed_share / reference_share))
        )
        standard_deviation = float(np.std(reference))
        scale = standard_deviation if standard_deviation > 1e-12 else 1e-12
        mean_shift = abs(float(np.mean(observed) - np.mean(reference))) / scale
        reports.append(
            FeatureDrift(
                feature_name=definition.name,
                population_stability_index=psi,
                standardized_mean_shift=mean_shift,
            )
        )
    maximum_psi = max(item.population_stability_index for item in reports)
    maximum_shift = max(item.standardized_mean_shift for item in reports)
    return DriftReport(
        feature_schema_sha256=feature_schema.sha256(),
        baseline_rows=len(baseline),
        current_rows=len(current),
        maximum_psi=maximum_psi,
        maximum_standardized_mean_shift=maximum_shift,
        drifted=maximum_psi > psi_threshold or maximum_shift > mean_shift_threshold,
        psi_threshold=psi_threshold,
        mean_shift_threshold=mean_shift_threshold,
        features=tuple(reports),
    )
