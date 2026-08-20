from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import ValidationError

from aiquanttrader.backtest.models import TimeWindow, WalkForwardFold, WindowRole
from aiquanttrader.features.models import MODEL_FEATURE_SCHEMA, FeatureSchema
from aiquanttrader.research.model_adapters import TrainedModel
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastTarget,
    ModelEngine,
    NoSignalControlReport,
    SearchPolicy,
    SearchTrial,
)
from aiquanttrader.research.search import (
    mean_squared_error,
    randomized_label_control,
    run_fold_retraining,
)


@dataclass
class FakeAdapter:
    engine: ModelEngine = ModelEngine.LIGHTGBM

    def train(
        self,
        matrix: CausalTrainingMatrix,
        *,
        target: ForecastTarget,
        parameters: dict[str, int | float | str | bool],
    ) -> TrainedModel:
        return TrainedModel(
            self.engine,
            target,
            matrix.feature_schema,
            parameters,
            float(parameters["multiplier"]),
        )

    def predict(self, model: TrainedModel, features: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(features[:, 0] * float(model.native_model), dtype=np.float64)

    def save(self, model: TrainedModel, path: Path) -> None:
        raise AssertionError("not used")

    def load(
        self, path: Path, *, target: ForecastTarget, feature_schema: FeatureSchema
    ) -> TrainedModel:
        raise AssertionError("not used")


def matrix(*, alter_test: bool = False) -> CausalTrainingMatrix:
    rows = 60
    features = np.zeros((rows, len(MODEL_FEATURE_SCHEMA.features)), dtype=np.float64)
    features[:, 0] = np.arange(rows, dtype=np.float64) / 10
    labels = features[:, 0] * 2
    if alter_test:
        labels[34:44] = -10_000
    timestamps = np.arange(rows, dtype=np.int64) * 10
    return CausalTrainingMatrix(
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 5,
        feature_schema=MODEL_FEATURE_SCHEMA,
        source_dataset_sha256="a" * 64,
    )


def fold() -> WalkForwardFold:
    return WalkForwardFold(
        fold=0,
        train=TimeWindow(role=WindowRole.TRAIN, start_ts_ns=0, end_ts_ns=200),
        purge=TimeWindow(role=WindowRole.PURGE, start_ts_ns=200, end_ts_ns=220),
        validation=TimeWindow(role=WindowRole.VALIDATION, start_ts_ns=220, end_ts_ns=320),
        embargo=TimeWindow(role=WindowRole.EMBARGO, start_ts_ns=320, end_ts_ns=340),
        test=TimeWindow(role=WindowRole.WALK_FORWARD_TEST, start_ts_ns=340, end_ts_ns=440),
    )


def policy() -> SearchPolicy:
    return SearchPolicy(
        policy_id="bounded-test",
        trials=(
            SearchTrial(trial_id="wrong", parameters={"multiplier": 1.0}),
            SearchTrial(trial_id="correct", parameters={"multiplier": 2.0}),
        ),
    )


def test_search_uses_validation_only_and_test_labels_cannot_change_selection() -> None:
    first = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=matrix(),
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
    )
    altered = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=matrix(alter_test=True),
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
    )
    assert first.search.receipt.selected_trial_id == "correct"
    assert altered.search.receipt.selected_trial_id == "correct"
    assert first.walk_forward_test_mse != altered.walk_forward_test_mse
    assert first.walk_forward_test_mse < first.zero_prediction_test_mse
    assert first.walk_forward_test_mse < first.training_mean_test_mse


def test_causal_windows_exclude_labels_crossing_the_boundary() -> None:
    source = matrix()
    training = source.window(fold().train)
    assert training.sample_ts_ns[-1] == 190
    assert training.label_end_ts_ns[-1] == 195

    crossing = CausalTrainingMatrix(
        features=source.features,
        labels=source.labels,
        sample_ts_ns=source.sample_ts_ns,
        label_end_ts_ns=source.label_end_ts_ns + 20,
        feature_schema=source.feature_schema,
        source_dataset_sha256=source.source_dataset_sha256,
    ).window(fold().train)
    assert crossing.sample_ts_ns[-1] == 170


def test_randomized_label_control_is_seeded_and_no_signal_is_mandatory() -> None:
    training = matrix().window(fold().train)
    validation = matrix().window(fold().validation)
    first = randomized_label_control(
        adapter=FakeAdapter(),
        training=training,
        validation=validation,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        selected_parameters={"multiplier": 1.0},
        minimum_mse=1,
        no_signal_decision_count=0,
        no_signal_report_sha256="f" * 64,
        seed=7,
    )
    second = randomized_label_control(
        adapter=FakeAdapter(),
        training=training,
        validation=validation,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        selected_parameters={"multiplier": 1.0},
        minimum_mse=1,
        no_signal_decision_count=0,
        no_signal_report_sha256="f" * 64,
        seed=7,
    )
    assert first == second
    assert first.passed
    assert not first.model_copy(update={"no_signal_decision_count": 1}).passed


def test_training_matrix_and_metric_reject_invalid_inputs() -> None:
    source = matrix()
    with pytest.raises(ValueError, match="read-only"):
        source.labels[0] = 99
    with pytest.raises(ValueError, match="strictly increasing"):
        CausalTrainingMatrix(
            features=source.features,
            labels=source.labels,
            sample_ts_ns=source.sample_ts_ns[::-1],
            label_end_ts_ns=source.label_end_ts_ns,
            feature_schema=source.feature_schema,
            source_dataset_sha256=source.source_dataset_sha256,
        )
    with pytest.raises(ValueError, match="aligned"):
        mean_squared_error(np.asarray([1.0]), np.asarray([1.0, 2.0]))


def test_no_signal_report_rejects_impossible_counts_and_windows() -> None:
    values = {
        "control_id": "neutral-alpha-order-flow-v1",
        "feature_dataset_sha256": "a" * 64,
        "feature_file_sha256": "b" * 64,
        "feature_schema_sha256": "c" * 64,
        "strategy_configuration_sha256": "d" * 64,
        "scenario_sha256": "e" * 64,
        "observation_count": 10,
        "ready_observation_count": 9,
        "decision_count": 0,
        "first_receive_ts_ns": 1,
        "last_receive_ts_ns": 2,
    }
    assert NoSignalControlReport.model_validate(values).schema_version == 2
    with pytest.raises(ValidationError, match="ready observations"):
        NoSignalControlReport.model_validate({**values, "ready_observation_count": 11})
    with pytest.raises(ValidationError, match="decisions"):
        NoSignalControlReport.model_validate({**values, "decision_count": 10})
    with pytest.raises(ValidationError, match="window is reversed"):
        NoSignalControlReport.model_validate(
            {**values, "first_receive_ts_ns": 3, "last_receive_ts_ns": 2}
        )
