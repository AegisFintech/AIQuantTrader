"""Bounded, validation-only hyperparameter search and negative controls."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from aiquanttrader.backtest.models import WalkForwardFold
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.research.model_adapters import ModelAdapter, TrainedModel
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastTarget,
    NegativeControlReport,
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
    score = mean_squared_error(
        test.labels,
        adapter.predict(search.selected_model, test.features),
    )
    zero_prediction_score = mean_squared_error(test.labels, np.zeros_like(test.labels))
    training_mean_score = mean_squared_error(
        test.labels,
        np.full_like(test.labels, float(np.mean(training.labels))),
    )
    return FoldResearchResult(
        search=search,
        walk_forward_test_mse=score,
        zero_prediction_test_mse=zero_prediction_score,
        training_mean_test_mse=training_mean_score,
        test_rows=len(test.labels),
    )


def randomized_label_control(
    *,
    adapter: ModelAdapter,
    training: CausalTrainingMatrix,
    validation: CausalTrainingMatrix,
    target: ForecastTarget,
    selected_parameters: dict[str, int | float | str | bool],
    minimum_mse: float,
    no_signal_decision_count: int,
    no_signal_report_sha256: str,
    seed: int,
) -> NegativeControlReport:
    if minimum_mse < 0 or no_signal_decision_count < 0 or seed < 0:
        raise ValueError("negative-control thresholds and counts must be non-negative")
    rng = np.random.default_rng(seed)
    shuffled = training.labels.copy()
    rng.shuffle(shuffled)
    randomized = CausalTrainingMatrix(
        features=training.features,
        labels=shuffled,
        sample_ts_ns=training.sample_ts_ns,
        label_end_ts_ns=training.label_end_ts_ns,
        feature_schema=training.feature_schema,
        source_dataset_sha256=training.source_dataset_sha256,
    )
    model = adapter.train(randomized, target=target, parameters=selected_parameters)
    score = mean_squared_error(validation.labels, adapter.predict(model, validation.features))
    return NegativeControlReport(
        randomized_label_score=score,
        randomized_label_minimum_mse=minimum_mse,
        no_signal_decision_count=no_signal_decision_count,
        no_signal_report_sha256=no_signal_report_sha256,
        randomized_seed=seed,
    )
