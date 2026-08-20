from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from pydantic import ValidationError

from aiquanttrader.backtest.models import (
    CalibrationState,
    ExecutionScenario,
    QueueModel,
    TimeWindow,
    ValidationPlan,
    WalkForwardFold,
    WindowRole,
)
from aiquanttrader.features.models import MODEL_FEATURE_SCHEMA, FeatureSchema, VolatilityRegime
from aiquanttrader.research.feasibility import (
    _maximum_non_overlapping_count,
    _maximum_non_overlapping_net,
    audit_target_feasibility,
    require_viable_target_feasibility,
)
from aiquanttrader.research.model_adapters import TrainedModel
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastEconomicPolicy,
    ForecastMatrixManifest,
    ForecastRegimePolicy,
    ForecastSlice,
    ForecastSliceMetrics,
    ForecastTarget,
    ModelEngine,
    NegativeControlReport,
    NoSignalControlReport,
    RandomizedLabelControlPolicy,
    ResearchControlPolicy,
    SearchPolicy,
    SearchTrial,
)
from aiquanttrader.research.search import (
    evaluate_forecast_economics,
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
    features[:, MODEL_FEATURE_SCHEMA.names.index("realized_volatility")] = np.arange(rows) % 3
    labels = features[:, 0] * 2
    if alter_test:
        labels[34:44] = -10_000
    timestamps = np.arange(rows, dtype=np.int64) * 10
    volatility_regimes = np.asarray(
        [
            (VolatilityRegime.LOW, VolatilityRegime.NORMAL, VolatilityRegime.HIGH)[index % 3].value
            for index in range(rows)
        ]
    )
    return CausalTrainingMatrix(
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 5,
        volatility_regimes=volatility_regimes,
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


def validation_plan() -> ValidationPlan:
    return ValidationPlan(
        policy_sha256="b" * 64,
        dataset_sha256="a" * 64,
        label_horizon_ns=5,
        folds=(fold(),),
        final_holdout=TimeWindow(
            role=WindowRole.FINAL_HOLDOUT,
            start_ts_ns=600,
            end_ts_ns=700,
        ),
    )


def matrix_manifest(source: CausalTrainingMatrix) -> ForecastMatrixManifest:
    regimes = source.volatility_regimes
    return ForecastMatrixManifest.create(
        partition_role="development",
        validation_plan_sha256=validation_plan().sha256(),
        development_cutoff_ts_ns=validation_plan().final_holdout.start_ts_ns,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=5,
        sample_interval_ns=10,
        maximum_label_delay_ns=0,
        source_feature_dataset_sha256="c" * 64,
        source_dataset_sha256=source.source_dataset_sha256,
        feature_schema_sha256=source.feature_schema.sha256(),
        causal_matrix_sha256=source.sha256(),
        file_sha256="d" * 64,
        source_row_count=len(source.labels),
        ready_row_count=len(source.labels),
        candidate_row_count=len(source.labels),
        row_count=len(source.labels),
        low_volatility_row_count=int(np.sum(regimes == VolatilityRegime.LOW.value)),
        normal_volatility_row_count=int(np.sum(regimes == VolatilityRegime.NORMAL.value)),
        high_volatility_row_count=int(np.sum(regimes == VolatilityRegime.HIGH.value)),
        dropped_label_gap_count=0,
        dropped_tail_count=0,
        excluded_holdout_candidate_count=0,
        first_sample_ts_ns=int(source.sample_ts_ns[0]),
        last_sample_ts_ns=int(source.sample_ts_ns[-1]),
        first_label_end_ts_ns=int(source.label_end_ts_ns[0]),
        last_label_end_ts_ns=int(source.label_end_ts_ns[-1]),
    )


def policy() -> SearchPolicy:
    return SearchPolicy(
        policy_id="bounded-test",
        trials=(
            SearchTrial(trial_id="wrong", parameters={"multiplier": 1.0}),
            SearchTrial(trial_id="correct", parameters={"multiplier": 2.0}),
        ),
    )


def control_policy() -> ResearchControlPolicy:
    return ResearchControlPolicy(
        policy_id="test-controls",
        randomized_label=RandomizedLabelControlPolicy(
            repetitions=3,
            base_seed=7,
            minimum_median_mse_multiple_of_selected_model=1.0,
            minimum_worst_mse_multiple_of_training_mean=0.5,
        ),
        forecast_regime=ForecastRegimePolicy(minimum_rows_per_slice=2),
        forecast_economic=ForecastEconomicPolicy(
            minimum_expected_edge_bps=0.0,
            minimum_trades=2,
            minimum_trades_per_regime=1,
            minimum_profit_factor=1.01,
            require_calibrated_scenario=False,
        ),
    )


def scenario() -> ExecutionScenario:
    return ExecutionScenario(
        scenario_id="zero-cost-test",
        calibration_state=CalibrationState.UNCALIBRATED,
        tick_size=Decimal("1"),
        lot_size=Decimal("0.00001"),
        entry_latency_ns=0,
        response_latency_ns=0,
        feed_latency_offset_ns=0,
        maker_fee_bps=Decimal("0"),
        taker_fee_bps=Decimal("0"),
        queue_model=QueueModel.RISK_ADVERSE,
        taker_slippage_bps=Decimal("0"),
    )


def test_search_uses_validation_only_and_test_labels_cannot_change_selection() -> None:
    first = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=matrix(),
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    altered = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=matrix(alter_test=True),
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    assert first.search.receipt.selected_trial_id == "correct"
    assert altered.search.receipt.selected_trial_id == "correct"
    assert first.walk_forward_test_mse != altered.walk_forward_test_mse
    assert first.walk_forward_test_mse < first.zero_prediction_test_mse
    assert first.walk_forward_test_mse < first.training_mean_test_mse
    assert first.forecast_robustness.passed
    assert not altered.forecast_robustness.passed
    assert first.forecast_economic.passed
    assert not altered.forecast_economic.passed


def test_target_feasibility_uses_training_only_and_exposes_optimistic_ceilings() -> None:
    source = matrix()
    report = audit_target_feasibility(
        matrix=source,
        matrix_manifest=matrix_manifest(source),
        validation_plan=validation_plan(),
        policy=control_policy(),
        scenario=scenario(),
    )
    altered_source = matrix(alter_test=True)
    altered = audit_target_feasibility(
        matrix=altered_source,
        matrix_manifest=matrix_manifest(altered_source),
        validation_plan=validation_plan(),
        policy=control_policy(),
        scenario=scenario(),
    )

    aggregate = report.folds[0].slices[0]
    assert report.folds == altered.folds
    assert aggregate.observation_count == 20
    assert aggregate.positive_net_label_count == 19
    assert aggregate.maximum_non_overlapping_observation_count == 20
    assert aggregate.maximum_non_overlapping_positive_net_count == 19
    assert aggregate.maximum_non_overlapping_net_return_bps == pytest.approx(38.0)
    assert aggregate.maximum_single_trade_net_return_bps == pytest.approx(3.8)
    assert report.opportunity_sufficient
    assert report.passed

    calibrated_gate = control_policy().model_copy(
        update={
            "forecast_economic": control_policy().forecast_economic.model_copy(
                update={"require_calibrated_scenario": True}
            )
        }
    )
    uncalibrated = audit_target_feasibility(
        matrix=source,
        matrix_manifest=matrix_manifest(source),
        validation_plan=validation_plan(),
        policy=calibrated_gate,
        scenario=scenario(),
    )
    assert uncalibrated.opportunity_sufficient
    assert not uncalibrated.passed
    require_viable_target_feasibility(
        report=uncalibrated,
        matrix=source,
        matrix_manifest=matrix_manifest(source),
        validation_plan=validation_plan(),
        policy=calibrated_gate,
        scenario=scenario(),
    )
    with pytest.raises(ValueError, match="does not match bound"):
        require_viable_target_feasibility(
            report=uncalibrated.model_copy(update={"scenario_id": "tampered"}),
            matrix=source,
            matrix_manifest=matrix_manifest(source),
            validation_plan=validation_plan(),
            policy=calibrated_gate,
            scenario=scenario(),
        )

    costly_scenario = scenario().model_copy(update={"taker_fee_bps": Decimal("100")})
    infeasible = audit_target_feasibility(
        matrix=source,
        matrix_manifest=matrix_manifest(source),
        validation_plan=validation_plan(),
        policy=control_policy(),
        scenario=costly_scenario,
    )
    assert not infeasible.opportunity_sufficient
    with pytest.raises(ValueError, match="opportunity ceiling"):
        require_viable_target_feasibility(
            report=infeasible,
            matrix=source,
            matrix_manifest=matrix_manifest(source),
            validation_plan=validation_plan(),
            policy=control_policy(),
            scenario=costly_scenario,
        )


def test_target_feasibility_interval_ceilings_are_deterministic() -> None:
    starts = np.asarray([0, 10, 20], dtype=np.int64)
    ends = np.asarray([20, 30, 40], dtype=np.int64)
    returns = np.asarray([5.0, 9.0, 5.0], dtype=np.float64)

    assert _maximum_non_overlapping_count(starts, ends) == 2
    assert _maximum_non_overlapping_net(starts, ends, returns) == 10.0
    with pytest.raises(ValueError, match="aligned vectors"):
        _maximum_non_overlapping_count(starts, ends[:-1])
    with pytest.raises(ValueError, match="must align"):
        _maximum_non_overlapping_net(starts, ends, returns[:-1])


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
        volatility_regimes=source.volatility_regimes,
        feature_schema=source.feature_schema,
        source_dataset_sha256=source.source_dataset_sha256,
    ).window(fold().train)
    assert crossing.sample_ts_ns[-1] == 170


def test_randomized_label_control_is_seeded_and_no_signal_is_mandatory() -> None:
    training = matrix().window(fold().train)
    validation = matrix().window(fold().validation)
    result = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=matrix(),
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    first = randomized_label_control(
        adapter=FakeAdapter(),
        training=training,
        validation=validation,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        selected_parameters={"multiplier": 2.0},
        search_receipt=result.search.receipt,
        policy=control_policy(),
        fold_index=0,
        no_signal_decision_count=0,
        no_signal_report_sha256="f" * 64,
        target_feasibility_report_sha256="e" * 64,
        target_feasibility_passed=True,
        forecast_robustness=result.forecast_robustness,
        forecast_economic=result.forecast_economic,
    )
    second = randomized_label_control(
        adapter=FakeAdapter(),
        training=training,
        validation=validation,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        selected_parameters={"multiplier": 2.0},
        search_receipt=result.search.receipt,
        policy=control_policy(),
        fold_index=0,
        no_signal_decision_count=0,
        no_signal_report_sha256="f" * 64,
        target_feasibility_report_sha256="e" * 64,
        target_feasibility_passed=True,
        forecast_robustness=result.forecast_robustness,
        forecast_economic=result.forecast_economic,
    )
    assert first == second
    assert first.randomized_seeds == (7, 8, 9)
    assert not first.passed
    with pytest.raises(ValueError, match="policy and fold"):
        randomized_label_control(
            adapter=FakeAdapter(),
            training=training,
            validation=validation,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            selected_parameters={"multiplier": 2.0},
            search_receipt=result.search.receipt,
            policy=control_policy(),
            fold_index=1,
            no_signal_decision_count=0,
            no_signal_report_sha256="f" * 64,
            target_feasibility_report_sha256="e" * 64,
            target_feasibility_passed=True,
            forecast_robustness=result.forecast_robustness,
            forecast_economic=result.forecast_economic,
        )
    mismatched_receipt_report = result.forecast_robustness.model_copy(
        update={"search_receipt_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="search receipt"):
        randomized_label_control(
            adapter=FakeAdapter(),
            training=training,
            validation=validation,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            selected_parameters={"multiplier": 2.0},
            search_receipt=result.search.receipt,
            policy=control_policy(),
            fold_index=0,
            no_signal_decision_count=0,
            no_signal_report_sha256="f" * 64,
            target_feasibility_report_sha256="e" * 64,
            target_feasibility_passed=True,
            forecast_robustness=mismatched_receipt_report,
            forecast_economic=result.forecast_economic,
        )
    with pytest.raises(ValueError, match="parameters do not match"):
        randomized_label_control(
            adapter=FakeAdapter(),
            training=training,
            validation=validation,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            selected_parameters={"multiplier": 1.0},
            search_receipt=result.search.receipt,
            policy=control_policy(),
            fold_index=0,
            no_signal_decision_count=0,
            no_signal_report_sha256="f" * 64,
            target_feasibility_report_sha256="e" * 64,
            target_feasibility_passed=True,
            forecast_robustness=result.forecast_robustness,
            forecast_economic=result.forecast_economic,
        )
    passing = first.model_copy(
        update={
            "randomized_label_scores": (1_000.0, 1_000.0, 1_000.0),
            "forecast_robustness_passed": True,
        }
    )
    assert passing.passed
    assert not passing.model_copy(update={"no_signal_decision_count": 1}).passed
    assert not passing.model_copy(update={"target_feasibility_passed": False}).passed
    with pytest.raises(ValidationError, match="seeds do not match"):
        NegativeControlReport.model_validate(
            {**first.model_dump(mode="python"), "randomized_seeds": (1, 2, 3)}
        )


def test_forecast_robustness_fails_closed_when_training_cannot_define_all_regimes() -> None:
    source = matrix()
    features = source.features.copy()
    features[:, MODEL_FEATURE_SCHEMA.names.index("realized_volatility")] = 1.0
    constant_volatility = CausalTrainingMatrix(
        features=features,
        labels=source.labels,
        sample_ts_ns=source.sample_ts_ns,
        label_end_ts_ns=source.label_end_ts_ns,
        volatility_regimes=np.full(len(source.labels), VolatilityRegime.LOW.value),
        feature_schema=source.feature_schema,
        source_dataset_sha256=source.source_dataset_sha256,
    )
    result = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=constant_volatility,
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    assert [item.row_count for item in result.forecast_robustness.slices] == [10, 10, 0, 0]
    assert not result.forecast_robustness.passed


def test_economic_replay_enforces_non_overlap_and_scenario_calibration() -> None:
    source = matrix()
    overlapping_source = CausalTrainingMatrix(
        features=source.features,
        labels=source.labels,
        sample_ts_ns=source.sample_ts_ns,
        label_end_ts_ns=source.label_end_ts_ns + 10,
        volatility_regimes=source.volatility_regimes,
        feature_schema=source.feature_schema,
        source_dataset_sha256=source.source_dataset_sha256,
    )
    result = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=source,
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    test = overlapping_source.window(fold().test)
    calibrated_gate = control_policy().model_copy(
        update={
            "forecast_economic": control_policy().forecast_economic.model_copy(
                update={"require_calibrated_scenario": True}
            )
        }
    )
    report = evaluate_forecast_economics(
        test=test,
        predictions=test.labels.copy(),
        fold=fold(),
        search_receipt=result.search.receipt,
        policy=calibrated_gate,
        scenario=scenario(),
    )

    assert report.slices[0].trade_count == 5
    assert report.overlapping_signal_count == 4
    assert report.below_threshold_count == 0
    assert report.performance_passed
    assert not report.passed
    with pytest.raises(ValidationError, match="signal threshold"):
        type(report).model_validate(
            {**report.model_dump(mode="python"), "signal_threshold_bps": 1.0}
        )


def test_economic_replay_charges_conservative_round_trip_taker_cost() -> None:
    source = matrix()
    result = run_fold_retraining(
        adapter=FakeAdapter(),
        matrix=source,
        fold=fold(),
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        policy=policy(),
        control_policy=control_policy(),
        scenario=scenario(),
    )
    test = source.window(fold().test)
    costly = scenario().model_copy(
        update={"taker_fee_bps": Decimal("1"), "taker_slippage_bps": Decimal("1")}
    )
    report = evaluate_forecast_economics(
        test=test,
        predictions=np.full_like(test.labels, 4.0),
        fold=fold(),
        search_receipt=result.search.receipt,
        policy=control_policy(),
        scenario=costly,
    )

    assert report.round_trip_cost_bps == 4.0
    assert report.signal_threshold_bps == 4.0
    assert report.slices[0].trade_count == 0
    assert report.below_threshold_count == len(test.labels)
    assert not report.performance_passed


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
            volatility_regimes=source.volatility_regimes,
            feature_schema=source.feature_schema,
            source_dataset_sha256=source.source_dataset_sha256,
        )
    with pytest.raises(ValueError, match="non-decreasing"):
        CausalTrainingMatrix(
            features=source.features,
            labels=source.labels,
            sample_ts_ns=source.sample_ts_ns,
            label_end_ts_ns=source.label_end_ts_ns[::-1],
            volatility_regimes=source.volatility_regimes,
            feature_schema=source.feature_schema,
            source_dataset_sha256=source.source_dataset_sha256,
        )
    with pytest.raises(ValueError, match="aligned"):
        mean_squared_error(np.asarray([1.0]), np.asarray([1.0, 2.0]))
    with pytest.raises(ValueError, match="finite"):
        mean_squared_error(np.asarray([float("nan")]), np.asarray([1.0]))
    with pytest.raises(ValueError, match="non-negative"):
        control_policy().randomized_label.seeds_for_fold(-1)
    with pytest.raises(ValueError, match="uint64"):
        control_policy().randomized_label.seeds_for_fold(2**63)
    with pytest.raises(ValidationError, match="empty forecast"):
        ForecastSliceMetrics(
            slice=ForecastSlice.LOW_VOLATILITY,
            row_count=0,
            model_mse=1.0,
        )
    with pytest.raises(ValidationError, match="populated forecast"):
        ForecastSliceMetrics(slice=ForecastSlice.HIGH_VOLATILITY, row_count=2)


def test_checked_research_control_policy_is_bounded(project_root: Path) -> None:
    checked = ResearchControlPolicy.model_validate_json(
        (project_root / "configs/research/controls-v2.json").read_bytes()
    )
    assert checked.randomized_label.repetitions == 3
    assert checked.randomized_label.seeds_for_fold(2) == (20260826, 20260827, 20260828)
    assert checked.forecast_regime.minimum_rows_per_slice == 100
    assert checked.schema_version == 2
    assert checked.policy_id == "forecast-integrity-v2"
    assert checked.forecast_economic.require_calibrated_scenario


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
