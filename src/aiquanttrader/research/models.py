"""Immutable research, artifact, control, drift, and promotion contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.backtest.models import TimeWindow
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.governance import PromotionStage
from aiquanttrader.features.models import FeatureSchema

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
    CATBOOST_CBM = "catboost_cbm"


MODEL_FORMAT_BY_ENGINE = {
    ModelEngine.LIGHTGBM: ModelFormat.LIGHTGBM_TEXT,
    ModelEngine.XGBOOST: ModelFormat.XGBOOST_JSON,
    ModelEngine.CATBOOST: ModelFormat.CATBOOST_CBM,
}

MODEL_SUFFIX_BY_FORMAT = {
    ModelFormat.LIGHTGBM_TEXT: ".txt",
    ModelFormat.XGBOOST_JSON: ".json",
    ModelFormat.CATBOOST_CBM: ".cbm",
}


class ModelArtifactManifest(DomainModel):
    schema_version: Literal[1] = 1
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
    feature_schema: FeatureSchema
    source_dataset_sha256: str

    def __post_init__(self) -> None:
        features = np.array(self.features, dtype=np.float64, order="C", copy=True)
        labels = np.array(self.labels, dtype=np.float64, order="C", copy=True)
        sample_ts_ns = np.array(self.sample_ts_ns, dtype=np.int64, order="C", copy=True)
        label_end_ts_ns = np.array(self.label_end_ts_ns, dtype=np.int64, order="C", copy=True)
        for values in (features, labels, sample_ts_ns, label_end_ts_ns):
            values.flags.writeable = False
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "sample_ts_ns", sample_ts_ns)
        object.__setattr__(self, "label_end_ts_ns", label_end_ts_ns)
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
            )
        ):
            raise ValueError("training labels and timestamps must align with feature rows")
        if not np.all(np.isfinite(self.features)) or not np.all(np.isfinite(self.labels)):
            raise ValueError("training matrix must contain only finite values")
        if not np.all(np.diff(self.sample_ts_ns) > 0):
            raise ValueError("sample timestamps must be strictly increasing")
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
            feature_schema=self.feature_schema,
            source_dataset_sha256=self.source_dataset_sha256,
        )


class ForecastMatrixManifest(DomainModel):
    """Immutable lineage for a leakage-safe supervised forecast matrix."""

    schema_version: Literal[1] = 1
    matrix_id: Sha256
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
    dropped_label_gap_count: int = Field(ge=0)
    dropped_tail_count: int = Field(ge=0)
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
        accounted = self.row_count + self.dropped_label_gap_count + self.dropped_tail_count
        if accounted != self.candidate_row_count:
            raise ValueError("forecast matrix candidate accounting does not balance")
        if self.last_sample_ts_ns <= self.first_sample_ts_ns:
            raise ValueError("forecast matrix sample window is not increasing")
        if self.first_label_end_ts_ns <= self.first_sample_ts_ns:
            raise ValueError("forecast matrix first label is not causal")
        if self.last_label_end_ts_ns <= self.last_sample_ts_ns:
            raise ValueError("forecast matrix last label is not causal")
        if self.last_label_end_ts_ns < self.first_label_end_ts_ns:
            raise ValueError("forecast matrix label window is reversed")
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
    schema_version: Literal[1] = 1
    feature_dataset_sha256: Sha256
    strategy_configuration_sha256: Sha256
    scenario_sha256: Sha256
    observation_count: int = Field(gt=0)
    decision_count: int = Field(ge=0)


class NegativeControlReport(DomainModel):
    randomized_label_score: NonNegativeMetric
    randomized_label_minimum_mse: NonNegativeMetric
    no_signal_decision_count: int = Field(ge=0)
    no_signal_report_sha256: Sha256
    randomized_seed: int = Field(ge=0)

    @property
    def passed(self) -> bool:
        return (
            self.randomized_label_score >= self.randomized_label_minimum_mse
            and self.no_signal_decision_count == 0
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
        return self
