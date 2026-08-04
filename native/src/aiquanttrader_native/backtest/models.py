"""Versioned backtest, execution-assumption, and validation contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
FeeBps = Annotated[Decimal, Field(ge=-100, le=100)]


class QueueModel(StrEnum):
    LOG_PROBABILITY = "log_probability"
    POWER_PROBABILITY = "power_probability"
    RISK_ADVERSE = "risk_adverse"


class CalibrationState(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"


class ExecutionScenario(DomainModel):
    """Every simulator assumption that can materially alter execution results."""

    schema_version: Literal[1] = 1
    scenario_id: Identifier
    calibration_state: CalibrationState
    calibration_sha256: Sha256 | None = None
    tick_size: PositiveDecimal
    lot_size: PositiveDecimal
    entry_latency_ns: int = Field(ge=0)
    response_latency_ns: int = Field(ge=0)
    feed_latency_offset_ns: int = Field(ge=0)
    maker_fee_bps: FeeBps
    taker_fee_bps: FeeBps
    queue_model: QueueModel
    queue_power: PositiveDecimal = Decimal("2")
    allow_partial_fills: bool = True
    book_liquidity_multiplier: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("1")
    trade_flow_multiplier: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("1")
    taker_slippage_bps: NonNegativeDecimal = Decimal("0")
    funding_rate_multiplier: NonNegativeDecimal = Decimal("1")

    @model_validator(mode="after")
    def require_calibration_identity(self) -> Self:
        if self.calibration_state is CalibrationState.CALIBRATED:
            if self.calibration_sha256 is None:
                raise ValueError("calibrated scenarios require a calibration hash")
        elif self.calibration_sha256 is not None:
            raise ValueError("uncalibrated scenarios cannot claim a calibration hash")
        return self

    @property
    def maker_fee_rate(self) -> float:
        return float(self.maker_fee_bps / Decimal("10000"))

    @property
    def taker_fee_rate(self) -> float:
        return float(self.taker_fee_bps / Decimal("10000"))

    def require_promotion_eligible(self) -> None:
        if self.calibration_state is not CalibrationState.CALIBRATED:
            raise ValueError("uncalibrated execution scenarios are not promotion eligible")


class SourceArtifact(DomainModel):
    relative_path: Annotated[str, Field(min_length=1, max_length=1024)]
    artifact_sha256: Sha256
    row_count: int = Field(ge=0)


class BacktestDatasetManifest(DomainModel):
    schema_version: Literal[1] = 1
    dataset_id: Sha256
    converter_version: Literal["hft-events-v1"] = "hft-events-v1"
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    source_kind: Literal["tardis", "normalized_parquet"]
    sources: tuple[SourceArtifact, ...] = Field(min_length=1)
    event_file: Annotated[str, Field(min_length=1, max_length=1024)]
    event_file_sha256: Sha256
    event_count: int = Field(gt=0)
    first_exchange_ts_ns: int = Field(ge=0)
    last_exchange_ts_ns: int = Field(ge=0)
    first_local_ts_ns: int = Field(ge=0)
    last_local_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_ranges_and_identity(self) -> Self:
        if self.last_exchange_ts_ns < self.first_exchange_ts_ns:
            raise ValueError("exchange timestamp range is reversed")
        if self.last_local_ts_ns < self.first_local_ts_ns:
            raise ValueError("local timestamp range is reversed")
        identity = {
            "converter_version": self.converter_version,
            "instrument_id": self.instrument_id,
            "source_kind": self.source_kind,
            "sources": [item.model_dump(mode="json") for item in self.sources],
            "event_file_sha256": self.event_file_sha256,
            "event_count": self.event_count,
            "first_exchange_ts_ns": self.first_exchange_ts_ns,
            "last_exchange_ts_ns": self.last_exchange_ts_ns,
            "first_local_ts_ns": self.first_local_ts_ns,
            "last_local_ts_ns": self.last_local_ts_ns,
        }
        from aiquanttrader_native.domain.base import canonical_sha256

        if canonical_sha256(identity) != self.dataset_id:
            raise ValueError("dataset_id does not match canonical dataset identity")
        return self


class WindowRole(StrEnum):
    TRAIN = "train"
    PURGE = "purge"
    VALIDATION = "validation"
    EMBARGO = "embargo"
    WALK_FORWARD_TEST = "walk_forward_test"
    FINAL_HOLDOUT = "final_holdout"


class TimeWindow(DomainModel):
    role: WindowRole
    start_ts_ns: int = Field(ge=0)
    end_ts_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def require_positive_duration(self) -> Self:
        if self.end_ts_ns <= self.start_ts_ns:
            raise ValueError("window end must follow start")
        return self

    def overlaps(self, other: TimeWindow) -> bool:
        return self.start_ts_ns < other.end_ts_ns and other.start_ts_ns < self.end_ts_ns


class WalkForwardFold(DomainModel):
    fold: int = Field(ge=0)
    train: TimeWindow
    purge: TimeWindow
    validation: TimeWindow
    embargo: TimeWindow
    test: TimeWindow

    @model_validator(mode="after")
    def enforce_order_and_roles(self) -> Self:
        windows = (self.train, self.purge, self.validation, self.embargo, self.test)
        expected = (
            WindowRole.TRAIN,
            WindowRole.PURGE,
            WindowRole.VALIDATION,
            WindowRole.EMBARGO,
            WindowRole.WALK_FORWARD_TEST,
        )
        if tuple(window.role for window in windows) != expected:
            raise ValueError("walk-forward windows have invalid roles")
        for left, right in pairwise(windows):
            if left.end_ts_ns != right.start_ts_ns or left.overlaps(right):
                raise ValueError("walk-forward windows must be contiguous and disjoint")
        return self


class ValidationPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    train_ns: int = Field(gt=0)
    purge_ns: int = Field(gt=0)
    validation_ns: int = Field(gt=0)
    embargo_ns: int = Field(gt=0)
    test_ns: int = Field(gt=0)
    step_ns: int = Field(gt=0)
    final_holdout_ns: int = Field(gt=0)
    label_horizon_ns: int = Field(gt=0)
    minimum_folds: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def guard_leakage_boundaries(self) -> Self:
        if self.purge_ns < self.label_horizon_ns:
            raise ValueError("purge must cover the complete label horizon")
        if self.step_ns < self.test_ns:
            raise ValueError("step must keep walk-forward test windows disjoint")
        return self


class ValidationPlan(DomainModel):
    schema_version: Literal[1] = 1
    policy_sha256: Sha256
    dataset_sha256: Sha256
    folds: tuple[WalkForwardFold, ...] = Field(min_length=1)
    final_holdout: TimeWindow

    @model_validator(mode="after")
    def holdout_is_untouched_by_folds(self) -> Self:
        if self.final_holdout.role is not WindowRole.FINAL_HOLDOUT:
            raise ValueError("final window must have final_holdout role")
        if any(fold.test.end_ts_ns > self.final_holdout.start_ts_ns for fold in self.folds):
            raise ValueError("walk-forward folds overlap the final holdout")
        return self


class SelectionReceipt(DomainModel):
    schema_version: Literal[1] = 1
    selected_candidate_id: Identifier
    candidate_set_sha256: Sha256
    validation_plan_sha256: Sha256
    selection_metric: Identifier
    selection_payload_sha256: Sha256


class FundingObservation(DomainModel):
    settlement_ts_ns: int = Field(ge=0)
    funding_rate: Decimal
    oracle_price: PositiveDecimal


class PositionObservation(DomainModel):
    ts_ns: int = Field(ge=0)
    position_base: Decimal


class FundingCashflow(DomainModel):
    settlement_ts_ns: int = Field(ge=0)
    position_base: Decimal
    funding_rate: Decimal
    oracle_price: PositiveDecimal
    cashflow_usd: Decimal


class FillObservation(DomainModel):
    local_ts_ns: int = Field(ge=0)
    order_id: int = Field(gt=0)
    side: Literal["buy", "sell"]
    quantity_base: PositiveDecimal
    price: PositiveDecimal
    maker: bool


class ReplayResult(DomainModel):
    schema_version: Literal[1] = 1
    dataset_sha256: Sha256
    scenario_sha256: Sha256
    ending_position_base: Decimal
    ending_mark_price: PositiveDecimal
    cash_balance_usd: Decimal
    exchange_fee_usd: Decimal
    explicit_slippage_usd: NonNegativeDecimal
    funding_cashflow_usd: Decimal
    marked_equity_usd: Decimal
    fills: tuple[FillObservation, ...]
    funding_cashflows: tuple[FundingCashflow, ...]
