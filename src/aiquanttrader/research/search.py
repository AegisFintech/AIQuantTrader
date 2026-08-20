"""Bounded, validation-only hyperparameter search and negative controls."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from aiquanttrader.backtest.models import ExecutionScenario, WalkForwardFold
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.research.model_adapters import ModelAdapter, TrainedModel
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastEconomicReport,
    ForecastEconomicSliceMetrics,
    ForecastRobustnessReport,
    ForecastSlice,
    ForecastSliceMetrics,
    ForecastTarget,
    NegativeControlReport,
    ResearchControlPolicy,
    SearchPolicy,
    SearchReceipt,
    TrialResult,
)


@dataclass(frozen=True, slots=True)
class SearchRun:
    selected_model: TrainedModel
    receipt: SearchReceipt


@dataclass(frozen=True, slots=True)
class FoldResearchResult:
    search: SearchRun
    walk_forward_test_mse: float
    zero_prediction_test_mse: float
    training_mean_test_mse: float
    forecast_robustness: ForecastRobustnessReport
    forecast_economic: ForecastEconomicReport
    test_rows: int


def mean_squared_error(actual: NDArray[np.float64], predicted: NDArray[np.float64]) -> float:
    if actual.shape != predicted.shape or actual.ndim != 1 or not len(actual):
        raise ValueError("metric vectors must be non-empty and aligned")
    if not np.all(np.isfinite(actual)) or not np.all(np.isfinite(predicted)):
        raise ValueError("metric vectors must be finite")
    return float(np.mean((actual - predicted) ** 2))


def run_bounded_search(
    *,
    adapter: ModelAdapter,
    training: CausalTrainingMatrix,
    validation: CausalTrainingMatrix,
    target: ForecastTarget,
    policy: SearchPolicy,
    training_window_sha256: str,
    validation_window_sha256: str,
) -> SearchRun:
    if training.feature_schema.sha256() != validation.feature_schema.sha256():
        raise ValueError("training and validation feature schemas differ")
    results: list[TrialResult] = []
    trained: dict[str, TrainedModel] = {}
    for trial in policy.trials:
        model = adapter.train(training, target=target, parameters=trial.parameters)
        score = mean_squared_error(validation.labels, adapter.predict(model, validation.features))
        trained[trial.trial_id] = model
        results.append(
            TrialResult(
                trial_id=trial.trial_id,
                parameters_sha256=canonical_sha256(trial.parameters),
                validation_score=score,
            )
        )
    selected = min(results, key=lambda item: (item.validation_score, item.trial_id))
    receipt = SearchReceipt(
        search_policy_sha256=policy.sha256(),
        training_dataset_sha256=training.sha256(),
        training_window_sha256=training_window_sha256,
        validation_window_sha256=validation_window_sha256,
        selected_trial_id=selected.trial_id,
        selected_parameters_sha256=selected.parameters_sha256,
        results=tuple(results),
    )
    return SearchRun(selected_model=trained[selected.trial_id], receipt=receipt)


def run_fold_retraining(
    *,
    adapter: ModelAdapter,
    matrix: CausalTrainingMatrix,
    fold: WalkForwardFold,
    target: ForecastTarget,
    policy: SearchPolicy,
    control_policy: ResearchControlPolicy,
    scenario: ExecutionScenario,
) -> FoldResearchResult:
    training = matrix.window(fold.train)
    validation = matrix.window(fold.validation)
    test = matrix.window(fold.test)
    search = run_bounded_search(
        adapter=adapter,
        training=training,
        validation=validation,
        target=target,
        policy=policy,
        training_window_sha256=fold.train.sha256(),
        validation_window_sha256=fold.validation.sha256(),
    )
    predictions = adapter.predict(search.selected_model, test.features)
    score = mean_squared_error(test.labels, predictions)
    zero_prediction_score = mean_squared_error(test.labels, np.zeros_like(test.labels))
    training_mean = float(np.mean(training.labels))
    training_mean_score = mean_squared_error(
        test.labels,
        np.full_like(test.labels, training_mean),
    )
    forecast_robustness = evaluate_forecast_robustness(
        training=training,
        test=test,
        predictions=predictions,
        fold=fold,
        search_receipt=search.receipt,
        policy=control_policy,
    )
    forecast_economic = evaluate_forecast_economics(
        test=test,
        predictions=predictions,
        fold=fold,
        search_receipt=search.receipt,
        policy=control_policy,
        scenario=scenario,
    )
    return FoldResearchResult(
        search=search,
        walk_forward_test_mse=score,
        zero_prediction_test_mse=zero_prediction_score,
        training_mean_test_mse=training_mean_score,
        forecast_robustness=forecast_robustness,
        forecast_economic=forecast_economic,
        test_rows=len(test.labels),
    )


def _slice_metrics(
    *,
    slice_id: ForecastSlice,
    mask: NDArray[np.bool_],
    labels: NDArray[np.float64],
    predictions: NDArray[np.float64],
    training_mean: float,
) -> ForecastSliceMetrics:
    rows = int(mask.sum())
    if rows == 0:
        return ForecastSliceMetrics(slice=slice_id, row_count=0)
    actual = labels[mask]
    return ForecastSliceMetrics(
        slice=slice_id,
        row_count=rows,
        model_mse=mean_squared_error(actual, predictions[mask]),
        zero_prediction_mse=mean_squared_error(actual, np.zeros_like(actual)),
        training_mean_mse=mean_squared_error(actual, np.full_like(actual, training_mean)),
    )


def evaluate_forecast_robustness(
    *,
    training: CausalTrainingMatrix,
    test: CausalTrainingMatrix,
    predictions: NDArray[np.float64],
    fold: WalkForwardFold,
    search_receipt: SearchReceipt,
    policy: ResearchControlPolicy,
) -> ForecastRobustnessReport:
    """Score untouched test predictions across causal semantic volatility regimes."""

    if predictions.shape != test.labels.shape:
        raise ValueError("forecast robustness predictions do not align with test labels")
    training_mean = float(np.mean(training.labels))
    masks = (
        np.ones(len(test.labels), dtype=np.bool_),
        test.volatility_regimes == VolatilityRegime.LOW.value,
        test.volatility_regimes == VolatilityRegime.NORMAL.value,
        test.volatility_regimes == VolatilityRegime.HIGH.value,
    )
    slices = tuple(
        _slice_metrics(
            slice_id=slice_id,
            mask=mask,
            labels=test.labels,
            predictions=predictions,
            training_mean=training_mean,
        )
        for slice_id, mask in zip(ForecastSlice, masks, strict=True)
    )
    return ForecastRobustnessReport(
        policy=policy,
        fold_index=fold.fold,
        search_receipt_sha256=search_receipt.sha256(),
        feature_schema_sha256=training.feature_schema.sha256(),
        training_window_sha256=fold.train.sha256(),
        test_window_sha256=fold.test.sha256(),
        test_dataset_sha256=test.sha256(),
        training_mean_label=training_mean,
        slices=slices,
    )


def _maximum_drawdown_bps(net_returns: NDArray[np.float64]) -> float:
    if not len(net_returns):
        return 0.0
    equity = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(net_returns)))
    peaks = np.maximum.accumulate(equity)
    return float(np.max(peaks - equity))


def _economic_slice_metrics(
    *,
    slice_id: ForecastSlice,
    mask: NDArray[np.bool_],
    gross_returns: NDArray[np.float64],
    net_returns: NDArray[np.float64],
    round_trip_cost_bps: float,
) -> ForecastEconomicSliceMetrics:
    selected_gross = gross_returns[mask]
    selected_net = net_returns[mask]
    count = len(selected_net)
    gross_total = float(np.sum(selected_gross))
    transaction_cost = round_trip_cost_bps * count
    net_total = gross_total - transaction_cost
    return ForecastEconomicSliceMetrics(
        slice=slice_id,
        trade_count=count,
        winning_trade_count=int(np.sum(selected_net > 0)),
        losing_trade_count=int(np.sum(selected_net < 0)),
        gross_directional_return_bps=gross_total,
        transaction_cost_bps=transaction_cost,
        net_return_bps=net_total,
        average_net_return_bps=None if count == 0 else net_total / count,
        net_profit_bps=float(np.sum(selected_net[selected_net > 0])),
        net_loss_bps=float(-np.sum(selected_net[selected_net < 0])),
        maximum_drawdown_bps=_maximum_drawdown_bps(selected_net),
    )


def evaluate_forecast_economics(
    *,
    test: CausalTrainingMatrix,
    predictions: NDArray[np.float64],
    fold: WalkForwardFold,
    search_receipt: SearchReceipt,
    policy: ResearchControlPolicy,
    scenario: ExecutionScenario,
) -> ForecastEconomicReport:
    """Run a non-overlapping directional cost screen over untouched predictions."""

    if predictions.shape != test.labels.shape or not np.all(np.isfinite(predictions)):
        raise ValueError("economic replay predictions must be finite and align with test labels")
    round_trip_cost_bps = 2 * (
        max(float(scenario.taker_fee_bps), 0.0) + float(scenario.taker_slippage_bps)
    )
    threshold = round_trip_cost_bps + policy.forecast_economic.minimum_expected_edge_bps
    gross_returns: list[float] = []
    net_returns: list[float] = []
    regimes: list[str] = []
    below_threshold = 0
    overlapping = 0
    last_exit_ts_ns: int | None = None
    for sample_ts_ns, label_end_ts_ns, prediction, actual, regime in zip(
        test.sample_ts_ns,
        test.label_end_ts_ns,
        predictions,
        test.labels,
        test.volatility_regimes,
        strict=True,
    ):
        if abs(float(prediction)) <= threshold:
            below_threshold += 1
            continue
        if last_exit_ts_ns is not None and int(sample_ts_ns) < last_exit_ts_ns:
            overlapping += 1
            continue
        gross = float(actual) if prediction > 0 else -float(actual)
        gross_returns.append(gross)
        net_returns.append(gross - round_trip_cost_bps)
        regimes.append(str(regime))
        last_exit_ts_ns = int(label_end_ts_ns)

    gross_array = np.asarray(gross_returns, dtype=np.float64)
    net_array = np.asarray(net_returns, dtype=np.float64)
    regime_array = np.asarray(regimes, dtype=np.dtype("U6"))
    masks = (
        np.ones(len(net_array), dtype=np.bool_),
        regime_array == VolatilityRegime.LOW.value,
        regime_array == VolatilityRegime.NORMAL.value,
        regime_array == VolatilityRegime.HIGH.value,
    )
    slices = tuple(
        _economic_slice_metrics(
            slice_id=slice_id,
            mask=mask,
            gross_returns=gross_array,
            net_returns=net_array,
            round_trip_cost_bps=round_trip_cost_bps,
        )
        for slice_id, mask in zip(ForecastSlice, masks, strict=True)
    )
    return ForecastEconomicReport(
        policy=policy,
        fold_index=fold.fold,
        search_receipt_sha256=search_receipt.sha256(),
        test_window_sha256=fold.test.sha256(),
        test_dataset_sha256=test.sha256(),
        scenario_id=scenario.scenario_id,
        scenario_sha256=scenario.sha256(),
        calibration_state=scenario.calibration_state,
        round_trip_cost_bps=round_trip_cost_bps,
        signal_threshold_bps=threshold,
        observation_count=len(test.labels),
        below_threshold_count=below_threshold,
        overlapping_signal_count=overlapping,
        slices=slices,
    )


def randomized_label_control(
    *,
    adapter: ModelAdapter,
    training: CausalTrainingMatrix,
    validation: CausalTrainingMatrix,
    target: ForecastTarget,
    selected_parameters: dict[str, int | float | str | bool],
    search_receipt: SearchReceipt,
    policy: ResearchControlPolicy,
    fold_index: int,
    no_signal_decision_count: int,
    no_signal_report_sha256: str,
    forecast_robustness: ForecastRobustnessReport,
    forecast_economic: ForecastEconomicReport,
) -> NegativeControlReport:
    if no_signal_decision_count < 0:
        raise ValueError("negative-control thresholds and counts must be non-negative")
    if forecast_robustness.policy != policy or forecast_robustness.fold_index != fold_index:
        raise ValueError("forecast robustness report does not match control policy and fold")
    if forecast_robustness.search_receipt_sha256 != search_receipt.sha256():
        raise ValueError("forecast robustness report does not match search receipt")
    if forecast_economic.policy != policy or forecast_economic.fold_index != fold_index:
        raise ValueError("forecast economic report does not match control policy and fold")
    if forecast_economic.search_receipt_sha256 != search_receipt.sha256():
        raise ValueError("forecast economic report does not match search receipt")
    if canonical_sha256(selected_parameters) != search_receipt.selected_parameters_sha256:
        raise ValueError("negative-control parameters do not match search receipt")
    selected_model_validation_mse = next(
        result.validation_score
        for result in search_receipt.results
        if result.trial_id == search_receipt.selected_trial_id
    )
    seeds = policy.randomized_label.seeds_for_fold(fold_index)
    scores: list[float] = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        shuffled = training.labels.copy()
        rng.shuffle(shuffled)
        randomized = CausalTrainingMatrix(
            features=training.features,
            labels=shuffled,
            sample_ts_ns=training.sample_ts_ns,
            label_end_ts_ns=training.label_end_ts_ns,
            volatility_regimes=training.volatility_regimes,
            feature_schema=training.feature_schema,
            source_dataset_sha256=training.source_dataset_sha256,
        )
        model = adapter.train(randomized, target=target, parameters=selected_parameters)
        scores.append(
            mean_squared_error(validation.labels, adapter.predict(model, validation.features))
        )
    training_mean_validation_mse = mean_squared_error(
        validation.labels,
        np.full_like(validation.labels, float(np.mean(training.labels))),
    )
    return NegativeControlReport(
        policy=policy,
        fold_index=fold_index,
        search_receipt_sha256=search_receipt.sha256(),
        selected_model_validation_mse=selected_model_validation_mse,
        training_mean_validation_mse=training_mean_validation_mse,
        randomized_label_scores=tuple(scores),
        randomized_seeds=seeds,
        no_signal_decision_count=no_signal_decision_count,
        no_signal_report_sha256=no_signal_report_sha256,
        forecast_robustness_report_sha256=forecast_robustness.sha256(),
        forecast_robustness_passed=forecast_robustness.passed,
        forecast_economic_report_sha256=forecast_economic.sha256(),
        forecast_economic_performance_passed=forecast_economic.performance_passed,
        forecast_economic_passed=forecast_economic.passed,
    )
