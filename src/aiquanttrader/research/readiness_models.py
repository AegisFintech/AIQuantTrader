"""Dependency-light contracts for continuous research-data readiness."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.data import DataQualityPolicy, MarketDataNamedCount

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class ResearchDataReadinessPolicy(DomainModel):
    """Frozen gates for deciding when retained public data can be sealed for research."""

    schema_version: Literal[1] = 1
    policy_id: Identifier
    maximum_contiguous_gap_ns: int = Field(ge=0)
    maximum_latest_segment_age_ns: int = Field(gt=0)
    maximum_excluded_frames: int = Field(ge=0)
    minimum_free_bytes: int = Field(gt=0)
    storage_projection_safety_bps: int = Field(default=12_500, ge=10_000, le=30_000)
    data_quality_policy: DataQualityPolicy

    @model_validator(mode="after")
    def align_gap_policy(self) -> Self:
        if self.maximum_contiguous_gap_ns != self.data_quality_policy.max_classified_gap_ns:
            raise ValueError("readiness and dataset quality gap bounds must match")
        return self


class ResearchDataReadinessGate(DomainModel):
    gate: Identifier
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=512)]
    required: Annotated[str, Field(min_length=1, max_length=512)]


class ResearchDataReadinessReport(DomainModel):
    """Content-addressed status of retained data; it never authorizes model training."""

    schema_version: Literal[1] = 1
    report_id: Sha256
    generated_ts_ns: int = Field(ge=0)
    policy: ResearchDataReadinessPolicy
    validation_policy: ValidationPolicy
    required_validation_span_ns: int = Field(gt=0)
    raw_manifest_count: int = Field(ge=0)
    normalized_manifest_count: int = Field(ge=0)
    paired_segment_count: int = Field(ge=0)
    invalid_manifest_count: int = Field(ge=0)
    invalid_binding_count: int = Field(ge=0)
    unpaired_raw_segment_count: int = Field(ge=0)
    orphan_normalized_segment_count: int = Field(ge=0)
    missing_normalized_file_count: int = Field(ge=0)
    overlap_count: int = Field(ge=0)
    continuity_break_count: int = Field(ge=0)
    contiguous_chain_count: int = Field(ge=0)
    latest_contiguous_started_ts_ns: int | None = Field(default=None, ge=0)
    latest_contiguous_ended_ts_ns: int | None = Field(default=None, ge=0)
    latest_contiguous_span_ns: int = Field(ge=0)
    longest_contiguous_span_ns: int = Field(ge=0)
    remaining_validation_span_ns: int = Field(ge=0)
    completion_bps: int = Field(ge=0, le=10_000)
    latest_segment_age_ns: int | None = Field(default=None, ge=0)
    latest_chain_segment_count: int = Field(ge=0)
    latest_chain_raw_records: int = Field(ge=0)
    latest_chain_normalized_events: int = Field(ge=0)
    latest_chain_excluded_frames: int = Field(ge=0)
    latest_chain_quality_issues: tuple[MarketDataNamedCount, ...]
    latest_chain_dataset_admitted: bool
    data_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    storage_rate_bytes_per_day: int = Field(ge=0)
    estimated_additional_bytes_required: int = Field(ge=0)
    storage_headroom_bytes: int
    gates: tuple[ResearchDataReadinessGate, ...] = Field(min_length=1)
    ready_for_horizon_audit: bool
    model_training_authorized: Literal[False] = False
    production_promotion_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity_and_verdict(self) -> Self:
        expected_span = (
            self.validation_policy.train_ns
            + self.validation_policy.purge_ns
            + self.validation_policy.validation_ns
            + self.validation_policy.embargo_ns
            + self.validation_policy.test_ns
            + (self.validation_policy.minimum_folds - 1) * self.validation_policy.step_ns
            + self.validation_policy.final_holdout_ns
        )
        if self.required_validation_span_ns != expected_span:
            raise ValueError("readiness span does not match the validation policy")
        bounds = (
            self.latest_contiguous_started_ts_ns,
            self.latest_contiguous_ended_ts_ns,
        )
        if (bounds[0] is None) != (bounds[1] is None):
            raise ValueError("latest contiguous bounds must be present together")
        if bounds[0] is None:
            if self.latest_contiguous_span_ns != 0 or self.latest_chain_segment_count != 0:
                raise ValueError("empty readiness report cannot claim a latest chain")
        elif bounds[1] is None or bounds[1] - bounds[0] != self.latest_contiguous_span_ns:
            raise ValueError("latest contiguous span does not match its bounds")
        if self.remaining_validation_span_ns != max(
            0, self.required_validation_span_ns - self.latest_contiguous_span_ns
        ):
            raise ValueError("remaining readiness span is inconsistent")
        if self.completion_bps != min(
            10_000,
            self.latest_contiguous_span_ns * 10_000 // self.required_validation_span_ns,
        ):
            raise ValueError("readiness completion does not match the retained span")
        if self.storage_headroom_bytes != self.disk_free_bytes - self.policy.minimum_free_bytes:
            raise ValueError("storage headroom does not match the configured reserve")
        if len({item.name for item in self.latest_chain_quality_issues}) != len(
            self.latest_chain_quality_issues
        ):
            raise ValueError("readiness quality issue names must be unique")
        if len({gate.gate for gate in self.gates}) != len(self.gates):
            raise ValueError("readiness gates must be unique")
        if self.ready_for_horizon_audit != all(gate.passed for gate in self.gates):
            raise ValueError("readiness verdict does not match its gates")
        expected_identity = canonical_sha256(
            self.model_dump(mode="json", exclude={"report_id", "ready_for_horizon_audit"})
        )
        if self.report_id != expected_identity:
            raise ValueError("readiness report identity does not match its contents")
        return self


class ResearchDataReadinessState(DomainModel):
    schema_version: Literal[1] = 1
    status: Literal["starting", "running", "stopped", "failed"]
    heartbeat_ts_ns: int = Field(ge=0)
    report: ResearchDataReadinessReport | None = None
    last_error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.status == "running" and (self.report is None or self.last_error_code is not None):
            raise ValueError("running readiness state requires a report and no error")
        if self.status == "failed" and self.last_error_code is None:
            raise ValueError("failed readiness state requires an error code")
        return self
