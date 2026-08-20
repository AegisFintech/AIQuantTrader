"""Pre-search economic feasibility ceilings over sealed training windows."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from aiquanttrader.backtest.models import ExecutionScenario, ValidationPlan
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.research.matrix import require_development_matrix_plan
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastMatrixManifest,
    ForecastSlice,
    ResearchControlPolicy,
    TargetFeasibilityFoldReport,
    TargetFeasibilityReport,
    TargetFeasibilitySliceMetrics,
)


def _maximum_non_overlapping_count(starts: NDArray[np.int64], ends: NDArray[np.int64]) -> int:
    """Return the interval-scheduling cardinality ceiling."""

    if starts.shape != ends.shape or starts.ndim != 1:
        raise ValueError("target-feasibility intervals must be aligned vectors")
    if not len(starts):
        return 0
    order = np.lexsort((starts, ends))
    selected = 0
    previous_end: int | None = None
    for index in order:
        start = int(starts[index])
        if previous_end is None or start >= previous_end:
            selected += 1
            previous_end = int(ends[index])
    return selected


def _maximum_non_overlapping_net(
    starts: NDArray[np.int64],
    ends: NDArray[np.int64],
    net_returns: NDArray[np.float64],
) -> float:
    """Return the weighted-interval maximum net-return ceiling."""

    if starts.shape != ends.shape or starts.shape != net_returns.shape or starts.ndim != 1:
        raise ValueError("target-feasibility intervals and returns must align")
    if not len(starts):
        return 0.0
    order = np.lexsort((starts, ends))
    ordered_starts = starts[order]
    ordered_ends = ends[order]
    ordered_returns = net_returns[order]
    predecessors = np.searchsorted(ordered_ends, ordered_starts, side="right") - 1
    best = np.zeros(len(order) + 1, dtype=np.float64)
    for position, (predecessor, net_return) in enumerate(
        zip(predecessors, ordered_returns, strict=True), start=1
    ):
        included = float(net_return) + best[int(predecessor) + 1]
        best[position] = max(best[position - 1], included)
    return float(best[-1])


def _slice_metrics(
    *,
    slice_id: ForecastSlice,
    row_mask: NDArray[np.bool_],
    matrix: CausalTrainingMatrix,
    round_trip_cost_bps: float,
) -> TargetFeasibilitySliceMetrics:
    absolute_returns = np.abs(matrix.labels)
    observation_indices = np.flatnonzero(row_mask)
    if len(observation_indices):
        maximum_observation_count = _maximum_non_overlapping_count(
            matrix.sample_ts_ns[observation_indices],
            matrix.label_end_ts_ns[observation_indices],
        )
    else:
        maximum_observation_count = 0
    positive_net_mask = row_mask & (absolute_returns > round_trip_cost_bps)
    positive_net_indices = np.flatnonzero(positive_net_mask)
    if len(positive_net_indices):
        starts = matrix.sample_ts_ns[positive_net_indices]
        ends = matrix.label_end_ts_ns[positive_net_indices]
        net_returns = absolute_returns[positive_net_indices] - round_trip_cost_bps
        maximum_positive_count = _maximum_non_overlapping_count(starts, ends)
        maximum_net = _maximum_non_overlapping_net(starts, ends, net_returns)
        maximum_single = float(np.max(net_returns))
    else:
        maximum_positive_count = 0
        maximum_net = 0.0
        maximum_single = None
    return TargetFeasibilitySliceMetrics(
        slice=slice_id,
        observation_count=len(observation_indices),
        positive_net_label_count=len(positive_net_indices),
        maximum_non_overlapping_observation_count=maximum_observation_count,
        maximum_non_overlapping_positive_net_count=maximum_positive_count,
        maximum_non_overlapping_net_return_bps=maximum_net,
        maximum_single_trade_net_return_bps=maximum_single,
    )


def audit_target_feasibility(
    *,
    matrix: CausalTrainingMatrix,
    matrix_manifest: ForecastMatrixManifest,
    validation_plan: ValidationPlan,
    policy: ResearchControlPolicy,
    scenario: ExecutionScenario,
) -> TargetFeasibilityReport:
    """Compute optimistic necessary-condition ceilings without opening test windows."""

    require_development_matrix_plan(matrix_manifest, validation_plan)
    if matrix.sha256() != matrix_manifest.causal_matrix_sha256:
        raise ValueError("target-feasibility matrix does not match its manifest")
    round_trip_cost_bps = 2 * (
        max(float(scenario.taker_fee_bps), 0.0) + float(scenario.taker_slippage_bps)
    )
    signal_threshold_bps = round_trip_cost_bps + policy.forecast_economic.minimum_expected_edge_bps
    folds: list[TargetFeasibilityFoldReport] = []
    for fold in validation_plan.folds:
        training = matrix.window(fold.train)
        masks = (
            np.ones(len(training.labels), dtype=np.bool_),
            training.volatility_regimes == VolatilityRegime.LOW.value,
            training.volatility_regimes == VolatilityRegime.NORMAL.value,
            training.volatility_regimes == VolatilityRegime.HIGH.value,
        )
        slices = tuple(
            _slice_metrics(
                slice_id=slice_id,
                row_mask=mask,
                matrix=training,
                round_trip_cost_bps=round_trip_cost_bps,
            )
            for slice_id, mask in zip(ForecastSlice, masks, strict=True)
        )
        folds.append(
            TargetFeasibilityFoldReport(
                fold_index=fold.fold,
                training_window_sha256=fold.train.sha256(),
                training_dataset_sha256=training.sha256(),
                training_start_ts_ns=fold.train.start_ts_ns,
                training_end_ts_ns=fold.train.end_ts_ns,
                slices=slices,
            )
        )
    return TargetFeasibilityReport(
        target=matrix_manifest.target,
        matrix_id=matrix_manifest.matrix_id,
        causal_matrix_sha256=matrix_manifest.causal_matrix_sha256,
        feature_schema_sha256=matrix_manifest.feature_schema_sha256,
        validation_plan_sha256=validation_plan.sha256(),
        horizon_ns=matrix_manifest.horizon_ns,
        sample_interval_ns=matrix_manifest.sample_interval_ns,
        policy=policy,
        scenario_id=scenario.scenario_id,
        scenario_sha256=scenario.sha256(),
        calibration_state=scenario.calibration_state,
        round_trip_cost_bps=round_trip_cost_bps,
        signal_threshold_bps=signal_threshold_bps,
        folds=tuple(folds),
    )


def require_viable_target_feasibility(
    *,
    report: TargetFeasibilityReport,
    matrix: CausalTrainingMatrix,
    matrix_manifest: ForecastMatrixManifest,
    validation_plan: ValidationPlan,
    policy: ResearchControlPolicy,
    scenario: ExecutionScenario,
) -> None:
    """Recompute and require exact sufficient opportunity ceilings before search."""

    expected = audit_target_feasibility(
        matrix=matrix,
        matrix_manifest=matrix_manifest,
        validation_plan=validation_plan,
        policy=policy,
        scenario=scenario,
    )
    if report != expected:
        raise ValueError("target-feasibility report does not match bound research inputs")
    if not report.opportunity_sufficient:
        raise ValueError("target-feasibility opportunity ceiling did not pass")
