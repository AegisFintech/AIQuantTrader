"""Immutable research, artifact, control, drift, and promotion contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import median
from typing import Annotated, Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.backtest.models import (
    CalibrationState,
    ExecutionScenario,
    TimeWindow,
    ValidationPlan,
    ValidationPolicy,
)
from aiquanttrader.backtest.validation import plan_walk_forward
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.governance import PromotionStage
from aiquanttrader.features.models import (
    FeatureDatasetManifest,
    FeatureSchema,
    VolatilityRegime,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
FiniteMetric = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeMetric = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ForecastTarget(StrEnum):
    NEXT_MID_RETURN_BPS = "next_mid_return_bps"
    PASSIVE_FILL_PROBABILITY = "passive_fill_probability"
    SPREAD_EXPANSION_BPS = "spread_expansion_bps"
    VOLATILITY_REGIME = "volatility_regime"


class ModelEngine(StrEnum):
    LIGHTGBM = "lightgbm"
    XGBOOST = "xgboost"
    CATBOOST = "catboost"


class ModelFormat(StrEnum):
    LIGHTGBM_TEXT = "lightgbm_text"
    XGBOOST_JSON = "xgboost_json"
    CATBOOST_JSON = "catboost_json"


MODEL_FORMAT_BY_ENGINE = {
    ModelEngine.LIGHTGBM: ModelFormat.LIGHTGBM_TEXT,
    ModelEngine.XGBOOST: ModelFormat.XGBOOST_JSON,
    ModelEngine.CATBOOST: ModelFormat.CATBOOST_JSON,
}

MODEL_SUFFIX_BY_FORMAT = {
    ModelFormat.LIGHTGBM_TEXT: ".txt",
    ModelFormat.XGBOOST_JSON: ".json",
    ModelFormat.CATBOOST_JSON: ".json",
}


class ModelArtifactManifest(DomainModel):
    schema_version: Literal[2] = 2
    model_id: Sha256
    engine: ModelEngine
    model_format: ModelFormat
    target: ForecastTarget
    relative_path: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_./=:-]+$")]
    artifact_sha256: Sha256
    artifact_bytes: int = Field(gt=0)
    feature_schema_sha256: Sha256
    training_dataset_sha256: Sha256
    training_window_sha256: Sha256
    parameters_sha256: Sha256
    dependency_lock_sha256: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def validate_identity_and_format(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("model artifact time must be timezone-aware")
        expected_format = MODEL_FORMAT_BY_ENGINE[self.engine]
        expected_suffix = MODEL_SUFFIX_BY_FORMAT[self.model_format]
        if self.model_format is not expected_format:
            raise ValueError("model format does not match model engine")
        if not self.relative_path.endswith(expected_suffix):
            raise ValueError("model artifact extension does not match native format")
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("model artifact path must be safe and relative")
        identity = {
            "engine": self.engine,
            "model_format": self.model_format,
            "target": self.target,
            "relative_path": self.relative_path,
            "artifact_sha256": self.artifact_sha256,
            "artifact_bytes": self.artifact_bytes,
            "feature_schema_sha256": self.feature_schema_sha256,
            "training_dataset_sha256": self.training_dataset_sha256,
            "training_window_sha256": self.training_window_sha256,
            "parameters_sha256": self.parameters_sha256,
            "dependency_lock_sha256": self.dependency_lock_sha256,
        }
        if canonical_sha256(identity) != self.model_id:
            raise ValueError("model_id does not match canonical artifact identity")
        return self


@dataclass(frozen=True, slots=True)
class CausalTrainingMatrix:
    features: NDArray[np.float64]
    labels: NDArray[np.float64]
    sample_ts_ns: NDArray[np.int64]
    label_end_ts_ns: NDArray[np.int64]
    volatility_regimes: NDArray[np.str_]
    feature_schema: FeatureSchema
    source_dataset_sha256: str

    def __post_init__(self) -> None:
        features = np.array(self.features, dtype=np.float64, order="C", copy=True)
        labels = np.array(self.labels, dtype=np.float64, order="C", copy=True)
        sample_ts_ns = np.array(self.sample_ts_ns, dtype=np.int64, order="C", copy=True)
        label_end_ts_ns = np.array(self.label_end_ts_ns, dtype=np.int64, order="C", copy=True)
        volatility_regimes = np.array(
            self.volatility_regimes, dtype=np.dtype("U6"), order="C", copy=True
        )
        for values in (features, labels, sample_ts_ns, label_end_ts_ns, volatility_regimes):
            values.flags.writeable = False
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "sample_ts_ns", sample_ts_ns)
        object.__setattr__(self, "label_end_ts_ns", label_end_ts_ns)
        object.__setattr__(self, "volatility_regimes", volatility_regimes)
        if len(self.source_dataset_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_dataset_sha256
        ):
            raise ValueError("training matrix source dataset must be a lowercase SHA-256")
        rows = self.features.shape[0] if self.features.ndim == 2 else -1
        if rows < 2 or self.features.shape[1] != len(self.feature_schema.features):
            raise ValueError("training features have invalid shape for schema")
        if any(
            values.ndim != 1 or len(values) != rows
            for values in (
                self.labels,
                self.sample_ts_ns,
                self.label_end_ts_ns,
                self.volatility_regimes,
            )
        ):
            raise ValueError(
                "training labels, regimes, and timestamps must align with feature rows"
            )
        allowed_regimes = {
            VolatilityRegime.LOW.value,
            VolatilityRegime.NORMAL.value,
            VolatilityRegime.HIGH.value,
        }
        if not set(self.volatility_regimes.tolist()) <= allowed_regimes:
            raise ValueError("training matrix contains an invalid or warmup volatility regime")
        if not np.all(np.isfinite(self.features)) or not np.all(np.isfinite(self.labels)):
            raise ValueError("training matrix must contain only finite values")
        if not np.all(np.diff(self.sample_ts_ns) > 0):
            raise ValueError("sample timestamps must be strictly increasing")
        if np.any(np.diff(self.label_end_ts_ns) < 0):
            raise ValueError("label end timestamps must be non-decreasing")
        if np.any(self.label_end_ts_ns <= self.sample_ts_ns):
            raise ValueError("every label must end after its feature observation")

    def sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.source_dataset_sha256.encode("ascii"))
        digest.update(self.feature_schema.canonical_bytes())
        for values in (
            self.features.astype("<f8", copy=False),
            self.labels.astype("<f8", copy=False),
            self.sample_ts_ns.astype("<i8", copy=False),
            self.label_end_ts_ns.astype("<i8", copy=False),
        ):
            digest.update(str(values.shape).encode("ascii"))
            digest.update(values.tobytes(order="C"))
        regime_bytes = "\n".join(self.volatility_regimes.tolist()).encode("ascii")
        digest.update(str(self.volatility_regimes.shape).encode("ascii"))
        digest.update(regime_bytes)
        return digest.hexdigest()

    def window(self, window: TimeWindow) -> CausalTrainingMatrix:
        mask = (self.sample_ts_ns >= window.start_ts_ns) & (
            self.label_end_ts_ns <= window.end_ts_ns
        )
        if int(mask.sum()) < 2:
            raise ValueError(f"window {window.role.value} has fewer than two causal samples")
        return CausalTrainingMatrix(
            features=self.features[mask],
            labels=self.labels[mask],
            sample_ts_ns=self.sample_ts_ns[mask],
            label_end_ts_ns=self.label_end_ts_ns[mask],
            volatility_regimes=self.volatility_regimes[mask],
            feature_schema=self.feature_schema,
            source_dataset_sha256=self.source_dataset_sha256,
        )


class ForecastMatrixManifest(DomainModel):
    """Immutable lineage for a leakage-safe supervised forecast matrix."""

    schema_version: Literal[3] = 3
    matrix_id: Sha256
    partition_role: Literal["development"]
    validation_plan_sha256: Sha256
    development_cutoff_ts_ns: int = Field(gt=0)
    target: Literal[ForecastTarget.NEXT_MID_RETURN_BPS]
    horizon_ns: int = Field(gt=0)
    sample_interval_ns: int = Field(gt=0)
    maximum_label_delay_ns: int = Field(ge=0)
    source_feature_dataset_sha256: Sha256
    source_dataset_sha256: Sha256
    feature_schema_sha256: Sha256
    causal_matrix_sha256: Sha256
    file_sha256: Sha256
    source_row_count: int = Field(gt=0)
    ready_row_count: int = Field(gt=0)
    candidate_row_count: int = Field(gt=0)
    row_count: int = Field(ge=2)
    low_volatility_row_count: int = Field(ge=0)
    normal_volatility_row_count: int = Field(ge=0)
    high_volatility_row_count: int = Field(ge=0)
    dropped_label_gap_count: int = Field(ge=0)
    dropped_tail_count: int = Field(ge=0)
    excluded_holdout_candidate_count: int = Field(ge=0)
    first_sample_ts_ns: int = Field(ge=0)
    last_sample_ts_ns: int = Field(ge=0)
    first_label_end_ts_ns: int = Field(ge=0)
    last_label_end_ts_ns: int = Field(ge=0)

    @classmethod
    def create(cls, **values: Any) -> Self:
        identity = dict(values)
        return cls(matrix_id=canonical_sha256(identity), **identity)

    @model_validator(mode="after")
    def validate_identity_and_counts(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"schema_version", "matrix_id"})
        if canonical_sha256(identity) != self.matrix_id:
            raise ValueError("forecast matrix ID does not match canonical lineage")
        if self.ready_row_count > self.source_row_count:
            raise ValueError("forecast matrix ready rows exceed source rows")
        if self.candidate_row_count > self.ready_row_count:
            raise ValueError("forecast matrix candidates exceed ready rows")
        accounted = (
            self.row_count
            + self.dropped_label_gap_count
            + self.dropped_tail_count
            + self.excluded_holdout_candidate_count
        )
        if accounted != self.candidate_row_count:
            raise ValueError("forecast matrix candidate accounting does not balance")
        regime_rows = (
            self.low_volatility_row_count
            + self.normal_volatility_row_count
            + self.high_volatility_row_count
        )
        if regime_rows != self.row_count:
            raise ValueError("forecast matrix volatility-regime accounting does not balance")
        if self.last_sample_ts_ns <= self.first_sample_ts_ns:
            raise ValueError("forecast matrix sample window is not increasing")
        if self.first_label_end_ts_ns <= self.first_sample_ts_ns:
            raise ValueError("forecast matrix first label is not causal")
        if self.last_label_end_ts_ns <= self.last_sample_ts_ns:
            raise ValueError("forecast matrix last label is not causal")
        if self.last_label_end_ts_ns < self.first_label_end_ts_ns:
            raise ValueError("forecast matrix label window is reversed")
        if self.last_sample_ts_ns >= self.development_cutoff_ts_ns:
            raise ValueError("forecast matrix samples reach the final holdout")
        if self.last_label_end_ts_ns >= self.development_cutoff_ts_ns:
            raise ValueError("forecast matrix labels reach the final holdout")
        return self


class SearchTrial(DomainModel):
    trial_id: Identifier
    parameters: dict[str, int | float | str | bool]


class SearchPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    trials: tuple[SearchTrial, ...] = Field(min_length=1, max_length=64)
    metric: Literal["mean_squared_error"] = "mean_squared_error"
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unique_trials(self) -> Self:
        ids = [trial.trial_id for trial in self.trials]
        if len(ids) != len(set(ids)):
            raise ValueError("search trial IDs must be unique")
        return self


class RandomizedLabelControlPolicy(DomainModel):
    repetitions: int = Field(default=3, ge=3, le=32)
    base_seed: int = Field(ge=0, le=4_294_967_295)
    minimum_median_mse_multiple_of_selected_model: Annotated[
        float, Field(ge=1, le=10, allow_inf_nan=False)
    ] = 1.0
    minimum_worst_mse_multiple_of_training_mean: Annotated[
        float, Field(ge=0.5, le=2, allow_inf_nan=False)
    ] = 0.95

    def seeds_for_fold(self, fold_index: int) -> tuple[int, ...]:
        if fold_index < 0:
            raise ValueError("fold index must be non-negative")
        first = self.base_seed + fold_index * self.repetitions
        last = first + self.repetitions - 1
        if last > 18_446_744_073_709_551_615:
            raise ValueError("derived randomized-label seed exceeds uint64")
        return tuple(range(first, last + 1))


class ForecastRegimePolicy(DomainModel):
    minimum_rows_per_slice: int = Field(default=100, ge=2)
    minimum_improvement_fraction_vs_zero: Annotated[
        float, Field(ge=0, lt=1, allow_inf_nan=False)
    ] = 0.0
    minimum_improvement_fraction_vs_training_mean: Annotated[
        float, Field(ge=0, lt=1, allow_inf_nan=False)
    ] = 0.0


class ForecastEconomicPolicy(DomainModel):
    execution_style: Literal["taker_round_trip"] = "taker_round_trip"
    minimum_expected_edge_bps: NonNegativeMetric = 0.5
    minimum_trades: int = Field(default=100, ge=1)
    minimum_trades_per_regime: int = Field(default=20, ge=1)
    minimum_net_return_bps: FiniteMetric = 0.0
    minimum_average_net_return_bps: FiniteMetric = 0.0
    minimum_profit_factor: Annotated[float, Field(gt=1, le=100, allow_inf_nan=False)] = 1.05
    require_calibrated_scenario: bool = True


class ResearchControlPolicy(DomainModel):
    schema_version: Literal[2] = 2
    policy_id: Identifier
    randomized_label: RandomizedLabelControlPolicy
    forecast_regime: ForecastRegimePolicy
    forecast_economic: ForecastEconomicPolicy


class ForecastSlice(StrEnum):
    AGGREGATE = "aggregate"
    LOW_VOLATILITY = "low_volatility"
    NORMAL_VOLATILITY = "normal_volatility"
    HIGH_VOLATILITY = "high_volatility"


class ForecastSliceMetrics(DomainModel):
    slice: ForecastSlice
    row_count: int = Field(ge=0)
    model_mse: NonNegativeMetric | None = None
    zero_prediction_mse: NonNegativeMetric | None = None
    training_mean_mse: NonNegativeMetric | None = None

    @model_validator(mode="after")
    def validate_metric_presence(self) -> Self:
        metrics = (self.model_mse, self.zero_prediction_mse, self.training_mean_mse)
        if self.row_count == 0 and any(value is not None for value in metrics):
            raise ValueError("empty forecast slices cannot declare metrics")
        if self.row_count > 0 and any(value is None for value in metrics):
            raise ValueError("populated forecast slices require every metric")
        return self

    def passes(self, policy: ForecastRegimePolicy) -> bool:
        if self.row_count < policy.minimum_rows_per_slice or self.model_mse is None:
            return False
        assert self.zero_prediction_mse is not None
        assert self.training_mean_mse is not None
        within_zero_limit = self.model_mse <= self.zero_prediction_mse * (
            1 - policy.minimum_improvement_fraction_vs_zero
        )
        within_training_mean_limit = self.model_mse <= self.training_mean_mse * (
            1 - policy.minimum_improvement_fraction_vs_training_mean
        )
        return (
            within_zero_limit
            and within_training_mean_limit
            and self.model_mse < self.zero_prediction_mse
            and self.model_mse < self.training_mean_mse
        )


class ForecastRobustnessReport(DomainModel):
    schema_version: Literal[2] = 2
    policy: ResearchControlPolicy
    fold_index: int = Field(ge=0)
    search_receipt_sha256: Sha256
    feature_schema_sha256: Sha256
    training_window_sha256: Sha256
    test_window_sha256: Sha256
    test_dataset_sha256: Sha256
    training_mean_label: FiniteMetric
    regime_source: Literal["causal_feature_snapshot"] = "causal_feature_snapshot"
    slices: tuple[ForecastSliceMetrics, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_slices(self) -> Self:
        expected = tuple(ForecastSlice)
        observed = tuple(item.slice for item in self.slices)
        if observed != expected:
            raise ValueError("forecast robustness slices must be complete and canonical")
        return self

    @property
    def passed(self) -> bool:
        return all(item.passes(self.policy.forecast_regime) for item in self.slices)


class ForecastEconomicSliceMetrics(DomainModel):
    slice: ForecastSlice
    trade_count: int = Field(ge=0)
    winning_trade_count: int = Field(ge=0)
    losing_trade_count: int = Field(ge=0)
    gross_directional_return_bps: FiniteMetric
    transaction_cost_bps: NonNegativeMetric
    net_return_bps: FiniteMetric
    average_net_return_bps: FiniteMetric | None = None
    net_profit_bps: NonNegativeMetric
    net_loss_bps: NonNegativeMetric
    maximum_drawdown_bps: NonNegativeMetric

    @model_validator(mode="after")
    def validate_trade_accounting(self) -> Self:
        if self.winning_trade_count + self.losing_trade_count > self.trade_count:
            raise ValueError("economic replay win/loss counts exceed trades")
        if self.trade_count == 0 and self.average_net_return_bps is not None:
            raise ValueError("empty economic replay slices cannot declare an average")
        if self.trade_count > 0 and self.average_net_return_bps is None:
            raise ValueError("populated economic replay slices require an average")
        if not math.isclose(
            self.gross_directional_return_bps - self.transaction_cost_bps,
            self.net_return_bps,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("economic replay return accounting does not balance")
        if not math.isclose(
            self.net_profit_bps - self.net_loss_bps,
            self.net_return_bps,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("economic replay profit/loss accounting does not balance")
        if self.trade_count == 0 and any(
            value != 0
            for value in (
                self.winning_trade_count,
                self.losing_trade_count,
                self.gross_directional_return_bps,
                self.transaction_cost_bps,
                self.net_return_bps,
                self.net_profit_bps,
                self.net_loss_bps,
                self.maximum_drawdown_bps,
            )
        ):
            raise ValueError("empty economic replay slices must have zero accounting")
        if self.trade_count > 0:
            assert self.average_net_return_bps is not None
            if not math.isclose(
                self.average_net_return_bps,
                self.net_return_bps / self.trade_count,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise ValueError("economic replay average does not match net return")
        if self.maximum_drawdown_bps > self.net_loss_bps + 1e-9:
            raise ValueError("economic replay drawdown exceeds total net losses")
        return self

    @property
    def profit_factor(self) -> float | None:
        if self.net_loss_bps == 0:
            return None
        return self.net_profit_bps / self.net_loss_bps

    def passes(self, policy: ForecastEconomicPolicy) -> bool:
        minimum_trades = (
            policy.minimum_trades
            if self.slice is ForecastSlice.AGGREGATE
            else policy.minimum_trades_per_regime
        )
        if self.trade_count < minimum_trades or self.average_net_return_bps is None:
            return False
        profit_factor_passed = (self.net_loss_bps == 0 and self.net_profit_bps > 0) or (
            self.profit_factor is not None and self.profit_factor >= policy.minimum_profit_factor
        )
        return (
            self.net_return_bps > policy.minimum_net_return_bps
            and self.average_net_return_bps > policy.minimum_average_net_return_bps
            and profit_factor_passed
        )


class ForecastEconomicReport(DomainModel):
    schema_version: Literal[1] = 1
    policy: ResearchControlPolicy
    fold_index: int = Field(ge=0)
    search_receipt_sha256: Sha256
    test_window_sha256: Sha256
    test_dataset_sha256: Sha256
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    round_trip_cost_bps: NonNegativeMetric
    signal_threshold_bps: NonNegativeMetric
    observation_count: int = Field(gt=0)
    below_threshold_count: int = Field(ge=0)
    overlapping_signal_count: int = Field(ge=0)
    slices: tuple[ForecastEconomicSliceMetrics, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_economic_report(self) -> Self:
        expected = tuple(ForecastSlice)
        observed = tuple(item.slice for item in self.slices)
        if observed != expected:
            raise ValueError("economic replay slices must be complete and canonical")
        aggregate = self.slices[0]
        accounted = (
            aggregate.trade_count + self.below_threshold_count + self.overlapping_signal_count
        )
        if accounted != self.observation_count:
            raise ValueError("economic replay observation accounting does not balance")
        if sum(item.trade_count for item in self.slices[1:]) != aggregate.trade_count:
            raise ValueError("economic replay regime trade accounting does not balance")
        if not math.isclose(
            self.signal_threshold_bps,
            self.round_trip_cost_bps + self.policy.forecast_economic.minimum_expected_edge_bps,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("economic replay signal threshold does not match policy and costs")
        for item in self.slices:
            if not math.isclose(
                item.transaction_cost_bps,
                self.round_trip_cost_bps * item.trade_count,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise ValueError("economic replay transaction costs do not match trade count")
        for field in (
            "winning_trade_count",
            "losing_trade_count",
            "gross_directional_return_bps",
            "transaction_cost_bps",
            "net_return_bps",
            "net_profit_bps",
            "net_loss_bps",
        ):
            aggregate_value = getattr(aggregate, field)
            regime_total = sum(getattr(item, field) for item in self.slices[1:])
            if isinstance(aggregate_value, int):
                matches = aggregate_value == regime_total
            else:
                matches = math.isclose(
                    aggregate_value,
                    regime_total,
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            if not matches:
                raise ValueError(f"economic replay regime {field} accounting does not balance")
        return self

    @property
    def performance_passed(self) -> bool:
        return all(item.passes(self.policy.forecast_economic) for item in self.slices)

    @property
    def passed(self) -> bool:
        calibrated = self.calibration_state is CalibrationState.CALIBRATED
        return self.performance_passed and (
            calibrated or not self.policy.forecast_economic.require_calibrated_scenario
        )


class TargetFeasibilitySliceMetrics(DomainModel):
    """Optimistic label-derived ceilings; never tradable performance evidence."""

    slice: ForecastSlice
    observation_count: int = Field(ge=0)
    positive_net_label_count: int = Field(ge=0)
    maximum_non_overlapping_observation_count: int = Field(ge=0)
    maximum_non_overlapping_positive_net_count: int = Field(ge=0)
    maximum_non_overlapping_net_return_bps: NonNegativeMetric
    maximum_single_trade_net_return_bps: NonNegativeMetric | None = None

    @model_validator(mode="after")
    def validate_ceilings(self) -> Self:
        if self.positive_net_label_count > self.observation_count:
            raise ValueError("target-feasibility opportunity counts do not balance")
        if not (
            self.maximum_non_overlapping_positive_net_count <= self.positive_net_label_count
        ) or not (
            self.maximum_non_overlapping_positive_net_count
            <= self.maximum_non_overlapping_observation_count
            <= self.observation_count
        ):
            raise ValueError("target-feasibility non-overlapping counts do not balance")
        if self.observation_count == 0 and self.maximum_non_overlapping_observation_count != 0:
            raise ValueError("empty target-feasibility slices cannot select observations")
        if self.observation_count > 0 and self.maximum_non_overlapping_observation_count == 0:
            raise ValueError("populated target-feasibility slices require an observation ceiling")
        if self.positive_net_label_count == 0:
            if (
                self.maximum_non_overlapping_positive_net_count != 0
                or self.maximum_non_overlapping_net_return_bps != 0
                or self.maximum_single_trade_net_return_bps is not None
            ):
                raise ValueError("empty target-feasibility slices must have zero ceilings")
        elif (
            self.maximum_non_overlapping_positive_net_count == 0
            or self.maximum_non_overlapping_net_return_bps <= 0
            or self.maximum_single_trade_net_return_bps is None
            or self.maximum_single_trade_net_return_bps <= 0
        ):
            raise ValueError("populated target-feasibility slices require positive ceilings")
        return self

    def necessary_conditions_possible(self, policy: ForecastEconomicPolicy) -> bool:
        minimum_trades = (
            policy.minimum_trades
            if self.slice is ForecastSlice.AGGREGATE
            else policy.minimum_trades_per_regime
        )
        return (
            self.maximum_non_overlapping_observation_count >= minimum_trades
            and self.maximum_non_overlapping_net_return_bps > policy.minimum_net_return_bps
            and self.maximum_single_trade_net_return_bps is not None
            and self.maximum_single_trade_net_return_bps > policy.minimum_average_net_return_bps
        )


class TargetFeasibilityFoldReport(DomainModel):
    fold_index: int = Field(ge=0)
    training_window_sha256: Sha256
    training_dataset_sha256: Sha256
    training_start_ts_ns: int = Field(ge=0)
    training_end_ts_ns: int = Field(gt=0)
    slices: tuple[TargetFeasibilitySliceMetrics, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_fold(self) -> Self:
        if self.training_end_ts_ns <= self.training_start_ts_ns:
            raise ValueError("target-feasibility training window must be positive")
        expected = tuple(ForecastSlice)
        observed = tuple(item.slice for item in self.slices)
        if observed != expected:
            raise ValueError("target-feasibility slices must be complete and canonical")
        aggregate = self.slices[0]
        if sum(item.observation_count for item in self.slices[1:]) != aggregate.observation_count:
            raise ValueError("target-feasibility regime observations do not balance")
        if (
            sum(item.positive_net_label_count for item in self.slices[1:])
            != aggregate.positive_net_label_count
        ):
            raise ValueError("target-feasibility regime opportunities do not balance")
        return self

    def necessary_conditions_possible(self, policy: ForecastEconomicPolicy) -> bool:
        return all(item.necessary_conditions_possible(policy) for item in self.slices)


class TargetFeasibilityReport(DomainModel):
    schema_version: Literal[1] = 1
    target: ForecastTarget
    matrix_id: Sha256
    causal_matrix_sha256: Sha256
    feature_schema_sha256: Sha256
    validation_plan_sha256: Sha256
    horizon_ns: int = Field(gt=0)
    sample_interval_ns: int = Field(gt=0)
    policy: ResearchControlPolicy
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    round_trip_cost_bps: NonNegativeMetric
    signal_threshold_bps: NonNegativeMetric
    selection_role: Literal["training_windows_only"] = "training_windows_only"
    oracle_kind: Literal["optimistic_non_overlapping_label_ceiling"] = (
        "optimistic_non_overlapping_label_ceiling"
    )
    folds: tuple[TargetFeasibilityFoldReport, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if not math.isclose(
            self.signal_threshold_bps,
            self.round_trip_cost_bps + self.policy.forecast_economic.minimum_expected_edge_bps,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("target-feasibility threshold does not match policy and costs")
        fold_indices = tuple(item.fold_index for item in self.folds)
        if fold_indices != tuple(range(len(self.folds))):
            raise ValueError("target-feasibility folds must be complete and canonical")
        return self

    @property
    def opportunity_sufficient(self) -> bool:
        return all(
            fold.necessary_conditions_possible(self.policy.forecast_economic) for fold in self.folds
        )

    @property
    def passed(self) -> bool:
        calibrated = self.calibration_state is CalibrationState.CALIBRATED
        return self.opportunity_sufficient and (
            calibrated or not self.policy.forecast_economic.require_calibrated_scenario
        )


class HorizonFamilyPolicy(DomainModel):
    """Predeclared, bounded scalping horizons; never a candidate selector."""

    schema_version: Literal[1] = 1
    policy_id: Identifier
    target: Literal[ForecastTarget.NEXT_MID_RETURN_BPS]
    horizons_ns: tuple[int, ...] = Field(min_length=2, max_length=8)
    sample_interval_ns: int = Field(gt=0)
    maximum_label_delay_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        if self.horizons_ns != tuple(sorted(set(self.horizons_ns))):
            raise ValueError("horizon family must be strictly increasing and unique")
        if self.horizons_ns[0] <= 0 or self.horizons_ns[-1] > 300_000_000_000:
            raise ValueError("horizon family must stay within the five-minute scalping bound")
        if self.sample_interval_ns > self.horizons_ns[0]:
            raise ValueError("horizon family sample interval cannot exceed its shortest horizon")
        return self


class HorizonFeasibilityCandidateReport(DomainModel):
    """Exact plan, matrix lineage, and optimistic audit for one frozen horizon."""

    horizon_ns: int = Field(gt=0, le=300_000_000_000)
    validation_policy: ValidationPolicy
    validation_plan: ValidationPlan
    matrix_manifest: ForecastMatrixManifest
    target_feasibility: TargetFeasibilityReport

    @model_validator(mode="after")
    def validate_candidate_lineage(self) -> Self:
        if self.validation_policy.label_horizon_ns != self.horizon_ns:
            raise ValueError("candidate validation policy has the wrong horizon")
        if self.validation_policy.purge_ns < self.horizon_ns:
            raise ValueError("candidate validation purge does not cover its horizon")
        if self.validation_plan.policy_sha256 != self.validation_policy.sha256():
            raise ValueError("candidate validation plan does not bind its policy")
        if self.validation_plan.label_horizon_ns != self.horizon_ns:
            raise ValueError("candidate validation plan has the wrong horizon")
        if self.matrix_manifest.horizon_ns != self.horizon_ns:
            raise ValueError("candidate matrix has the wrong horizon")
        if self.matrix_manifest.validation_plan_sha256 != self.validation_plan.sha256():
            raise ValueError("candidate matrix does not bind its validation plan")
        if self.target_feasibility.horizon_ns != self.horizon_ns:
            raise ValueError("candidate target-feasibility report has the wrong horizon")
        if self.target_feasibility.matrix_id != self.matrix_manifest.matrix_id:
            raise ValueError("candidate target-feasibility report does not bind its matrix")
        if self.target_feasibility.validation_plan_sha256 != self.validation_plan.sha256():
            raise ValueError("candidate target-feasibility report does not bind its plan")
        return self


class HorizonFamilyFeasibilityReport(DomainModel):
    """No-selection audit across an immutable, predeclared scalping horizon family."""

    schema_version: Literal[1] = 1
    policy: HorizonFamilyPolicy
    validation_template: ValidationPolicy
    feature_manifest: FeatureDatasetManifest
    control_policy: ResearchControlPolicy
    scenario: ExecutionScenario
    selection_role: Literal["predeclared_diagnostic_only"] = "predeclared_diagnostic_only"
    final_holdout_included: Literal[False] = False
    model_training_performed: Literal[False] = False
    candidates: tuple[HorizonFeasibilityCandidateReport, ...] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_family_lineage(self) -> Self:
        horizons = tuple(candidate.horizon_ns for candidate in self.candidates)
        if horizons != self.policy.horizons_ns:
            raise ValueError("horizon candidates do not match the predeclared family")
        holdouts = {
            candidate.validation_plan.final_holdout.sha256() for candidate in self.candidates
        }
        if len(holdouts) != 1:
            raise ValueError("horizon candidates must share one final holdout")
        for candidate in self.candidates:
            expected_policy_values = self.validation_template.model_dump(mode="python")
            expected_policy_values.update(
                {
                    "policy_id": (f"{self.validation_template.policy_id}.h{candidate.horizon_ns}"),
                    "label_horizon_ns": candidate.horizon_ns,
                    "purge_ns": max(self.validation_template.purge_ns, candidate.horizon_ns),
                }
            )
            expected_policy = ValidationPolicy.model_validate(expected_policy_values)
            if candidate.validation_policy != expected_policy:
                raise ValueError("horizon candidate policy was not derived from the template")
            expected_plan = plan_walk_forward(
                dataset_sha256=self.feature_manifest.source_dataset_sha256,
                start_ts_ns=self.feature_manifest.first_receive_ts_ns,
                end_ts_ns=self.feature_manifest.last_receive_ts_ns + 1,
                policy=expected_policy,
            )
            if candidate.validation_plan != expected_plan:
                raise ValueError("horizon candidate plan was not derived from feature bounds")
            matrix = candidate.matrix_manifest
            feasibility = candidate.target_feasibility
            if matrix.source_feature_dataset_sha256 != self.feature_manifest.feature_dataset_id:
                raise ValueError("horizon candidate does not bind the feature dataset")
            if matrix.source_dataset_sha256 != self.feature_manifest.source_dataset_sha256:
                raise ValueError("horizon candidate does not bind the source dataset")
            if matrix.feature_schema_sha256 != self.feature_manifest.feature_schema_sha256:
                raise ValueError("horizon candidate does not bind the feature schema")
            if (
                matrix.target is not self.policy.target
                or feasibility.target is not self.policy.target
            ):
                raise ValueError("horizon candidate does not bind the declared target")
            if matrix.sample_interval_ns != self.policy.sample_interval_ns:
                raise ValueError("horizon candidate does not bind the declared sample interval")
            if matrix.maximum_label_delay_ns != self.policy.maximum_label_delay_ns:
                raise ValueError("horizon candidate does not bind the declared label delay")
            if (
                candidate.validation_plan.dataset_sha256
                != self.feature_manifest.source_dataset_sha256
            ):
                raise ValueError("horizon validation plan does not bind the source dataset")
            if feasibility.policy != self.control_policy:
                raise ValueError("horizon candidate does not bind the control policy")
            if feasibility.scenario_sha256 != self.scenario.sha256():
                raise ValueError("horizon candidate does not bind the execution scenario")
        return self

    @property
    def opportunity_sufficient_horizons_ns(self) -> tuple[int, ...]:
        return tuple(
            candidate.horizon_ns
            for candidate in self.candidates
            if candidate.target_feasibility.opportunity_sufficient
        )

    @property
    def passed_horizons_ns(self) -> tuple[int, ...]:
        return tuple(
            candidate.horizon_ns
            for candidate in self.candidates
            if candidate.target_feasibility.passed
        )


class TrialResult(DomainModel):
    trial_id: Identifier
    parameters_sha256: Sha256
    validation_score: NonNegativeMetric


class SearchReceipt(DomainModel):
    schema_version: Literal[1] = 1
    search_policy_sha256: Sha256
    training_dataset_sha256: Sha256
    training_window_sha256: Sha256
    validation_window_sha256: Sha256
    selected_trial_id: Identifier
    selected_parameters_sha256: Sha256
    results: tuple[TrialResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        by_id = {result.trial_id: result for result in self.results}
        if len(by_id) != len(self.results):
            raise ValueError("search receipt trial results must be unique")
        selected = by_id.get(self.selected_trial_id)
        if selected is None:
            raise ValueError("selected search trial is absent from results")
        if selected.parameters_sha256 != self.selected_parameters_sha256:
            raise ValueError("selected parameter hash does not match trial result")
        return self


class NoSignalControlReport(DomainModel):
    schema_version: Literal[2] = 2
    control_id: Literal["neutral-alpha-order-flow-v1"]
    feature_dataset_sha256: Sha256
    feature_file_sha256: Sha256
    feature_schema_sha256: Sha256
    strategy_configuration_sha256: Sha256
    scenario_sha256: Sha256
    observation_count: int = Field(gt=0)
    ready_observation_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    first_receive_ts_ns: int = Field(ge=0)
    last_receive_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_control_counts_and_window(self) -> Self:
        if self.ready_observation_count > self.observation_count:
            raise ValueError("ready observations cannot exceed total observations")
        if self.decision_count > self.ready_observation_count:
            raise ValueError("no-signal decisions cannot exceed ready observations")
        if self.last_receive_ts_ns < self.first_receive_ts_ns:
            raise ValueError("no-signal observation window is reversed")
        return self


class NegativeControlReport(DomainModel):
    schema_version: Literal[4] = 4
    policy: ResearchControlPolicy
    fold_index: int = Field(ge=0)
    search_receipt_sha256: Sha256
    selected_model_validation_mse: NonNegativeMetric
    training_mean_validation_mse: NonNegativeMetric
    randomized_label_scores: tuple[NonNegativeMetric, ...] = Field(min_length=3, max_length=32)
    randomized_seeds: tuple[int, ...] = Field(min_length=3, max_length=32)
    no_signal_decision_count: int = Field(ge=0)
    no_signal_report_sha256: Sha256
    target_feasibility_report_sha256: Sha256
    target_feasibility_passed: bool
    forecast_robustness_report_sha256: Sha256
    forecast_robustness_passed: bool
    forecast_economic_report_sha256: Sha256
    forecast_economic_performance_passed: bool
    forecast_economic_passed: bool

    @model_validator(mode="after")
    def validate_randomized_label_evidence(self) -> Self:
        repetitions = self.policy.randomized_label.repetitions
        if len(self.randomized_label_scores) != repetitions:
            raise ValueError("randomized-label scores do not match policy repetitions")
        expected_seeds = self.policy.randomized_label.seeds_for_fold(self.fold_index)
        if self.randomized_seeds != expected_seeds:
            raise ValueError("randomized-label seeds do not match policy and fold")
        return self

    @property
    def randomized_label_median_mse(self) -> float:
        return float(median(self.randomized_label_scores))

    @property
    def randomized_label_worst_case_mse(self) -> float:
        return min(self.randomized_label_scores)

    @property
    def passed(self) -> bool:
        randomized = self.policy.randomized_label
        return (
            self.randomized_label_median_mse
            >= self.selected_model_validation_mse
            * randomized.minimum_median_mse_multiple_of_selected_model
            and self.randomized_label_worst_case_mse
            >= self.training_mean_validation_mse
            * randomized.minimum_worst_mse_multiple_of_training_mean
            and self.no_signal_decision_count == 0
            and self.target_feasibility_passed
            and self.forecast_robustness_passed
            and self.forecast_economic_passed
        )


class PromotionMetrics(DomainModel):
    post_cost_pnl_usd: FiniteMetric
    maximum_drawdown_usd: NonNegativeMetric
    tail_loss_99_usd: NonNegativeMetric
    maximum_abs_inventory_base: NonNegativeMetric
    fill_count: int = Field(ge=0)
    maker_ratio: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    adverse_selection_bps: FiniteMetric
    decision_latency_p99_ms: NonNegativeMetric
    fold_consistency: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    drift_psi_max: NonNegativeMetric
    operational_failure_count: int = Field(ge=0)


class PromotionPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    minimum_post_cost_pnl_usd: FiniteMetric
    maximum_drawdown_usd: NonNegativeMetric
    maximum_tail_loss_99_usd: NonNegativeMetric
    maximum_abs_inventory_base: NonNegativeMetric
    minimum_fill_count: int = Field(ge=1)
    minimum_maker_ratio: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    maximum_adverse_selection_bps: NonNegativeMetric
    maximum_decision_latency_p99_ms: NonNegativeMetric
    minimum_fold_consistency: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    maximum_drift_psi: NonNegativeMetric
    minimum_champion_improvement_usd: NonNegativeMetric
    require_negative_controls: bool = True


class GateResult(DomainModel):
    gate: Identifier
    passed: bool
    observed: FiniteMetric | int | bool
    threshold: FiniteMetric | int | bool


class ChampionChallengerReport(DomainModel):
    schema_version: Literal[1] = 1
    challenger_experiment_id: Identifier
    champion_experiment_id: Identifier | None = None
    policy_sha256: Sha256
    challenger_metrics_sha256: Sha256
    champion_metrics_sha256: Sha256 | None = None
    negative_controls_sha256: Sha256
    gates: tuple[GateResult, ...] = Field(min_length=1)
    passed: bool
    maximum_automation_stage: Literal[PromotionStage.AWAITING_APPROVAL] = (
        PromotionStage.AWAITING_APPROVAL
    )

    @model_validator(mode="after")
    def validate_gate_result(self) -> Self:
        gate_names = [gate.gate for gate in self.gates]
        if len(gate_names) != len(set(gate_names)):
            raise ValueError("promotion report gates must be unique")
        if self.passed != all(gate.passed for gate in self.gates):
            raise ValueError("promotion report outcome does not match its gates")
        if (self.champion_experiment_id is None) != (self.champion_metrics_sha256 is None):
            raise ValueError("champion identity and metrics hash must be supplied together")
        return self


class FeatureDrift(DomainModel):
    feature_name: Identifier
    population_stability_index: NonNegativeMetric
    standardized_mean_shift: NonNegativeMetric


class DriftReport(DomainModel):
    schema_version: Literal[1] = 1
    feature_schema_sha256: Sha256
    baseline_rows: int = Field(gt=0)
    current_rows: int = Field(gt=0)
    maximum_psi: NonNegativeMetric
    maximum_standardized_mean_shift: NonNegativeMetric
    drifted: bool
    psi_threshold: NonNegativeMetric
    mean_shift_threshold: NonNegativeMetric
    features: tuple[FeatureDrift, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        names = [feature.feature_name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("drift report feature names must be unique")
        if self.maximum_psi != max(feature.population_stability_index for feature in self.features):
            raise ValueError("drift report maximum PSI does not match feature results")
        if self.maximum_standardized_mean_shift != max(
            feature.standardized_mean_shift for feature in self.features
        ):
            raise ValueError("drift report maximum mean shift does not match feature results")
        expected = (
            self.maximum_psi > self.psi_threshold
            or self.maximum_standardized_mean_shift > self.mean_shift_threshold
        )
        if self.drifted != expected:
            raise ValueError("drift report outcome does not match its thresholds")
        return self


class ResearchExperimentManifest(DomainModel):
    schema_version: Literal[1] = 1
    experiment_id: Identifier
    created_at: datetime
    stage: PromotionStage
    strategy_id: Identifier
    code_sha256: Sha256
    dataset_sha256: Sha256
    feature_schema_sha256: Sha256
    configuration_sha256: Sha256
    dependency_lock_sha256: Sha256
    model_sha256: Sha256
    search_receipt_sha256: Sha256
    validation_plan_sha256: Sha256
    scenario_sha256s: tuple[Sha256, ...] = Field(min_length=2)
    parameters: dict[str, Any]
    metrics: PromotionMetrics
    negative_controls: NegativeControlReport
    report_sha256: Sha256

    @model_validator(mode="after")
    def validate_experiment(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("experiment timestamp must be timezone-aware")
        if len(set(self.scenario_sha256s)) != len(self.scenario_sha256s):
            raise ValueError("experiment scenarios must be unique")
        if self.negative_controls.search_receipt_sha256 != self.search_receipt_sha256:
            raise ValueError("experiment controls do not match search receipt")
        return self
