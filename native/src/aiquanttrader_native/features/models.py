"""Versioned feature configuration, schema, and snapshot contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
UnitDecimal = Annotated[Decimal, Field(ge=-1, le=1)]


class VolatilityRegime(StrEnum):
    WARMUP = "warmup"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class FeatureDefinition(DomainModel):
    name: Identifier
    dtype: Literal["float64"] = "float64"


class FeatureSchema(DomainModel):
    schema_version: Literal[1] = 1
    feature_set: Identifier
    features: tuple[FeatureDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_names(self) -> Self:
        names = [item.name for item in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature schema names must be unique")
        return self

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.features)


class FeatureDatasetManifest(DomainModel):
    schema_version: Literal[1] = 1
    feature_dataset_id: Sha256
    source_dataset_sha256: Sha256
    feature_schema_sha256: Sha256
    feature_config_sha256: Sha256
    relative_path: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_./=:-]+$")]
    file_sha256: Sha256
    row_count: int = Field(gt=0)
    first_receive_ts_ns: int = Field(ge=0)
    last_receive_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_feature_dataset_identity(self) -> Self:
        if self.last_receive_ts_ns < self.first_receive_ts_ns:
            raise ValueError("feature dataset timestamp range is reversed")
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("feature dataset path must be safe and relative")
        identity = {
            "source_dataset_sha256": self.source_dataset_sha256,
            "feature_schema_sha256": self.feature_schema_sha256,
            "feature_config_sha256": self.feature_config_sha256,
            "relative_path": self.relative_path,
            "file_sha256": self.file_sha256,
            "row_count": self.row_count,
            "first_receive_ts_ns": self.first_receive_ts_ns,
            "last_receive_ts_ns": self.last_receive_ts_ns,
        }
        if canonical_sha256(identity) != self.feature_dataset_id:
            raise ValueError("feature_dataset_id does not match canonical identity")
        return self


MODEL_FEATURE_NAMES = (
    "book_imbalance",
    "queue_imbalance",
    "depth_imbalance",
    "trade_flow_imbalance",
    "aggressor_ratio",
    "volume_delta",
    "realized_volatility",
    "atr_bps",
    "spread_bps",
    "spread_zscore",
    "mid_return_bps",
    "adverse_selection_bps",
)

MODEL_FEATURE_SCHEMA = FeatureSchema(
    feature_set="btc-microstructure-v1",
    features=tuple(FeatureDefinition(name=name) for name in MODEL_FEATURE_NAMES),
)


class FeatureEngineConfig(DomainModel):
    schema_version: Literal[1] = 1
    feature_set: Literal["btc-microstructure-v1"] = "btc-microstructure-v1"
    depth_levels: int = Field(default=10, ge=1, le=10)
    flow_window_ns: int = Field(default=5_000_000_000, gt=0)
    volatility_window_ns: int = Field(default=30_000_000_000, gt=0)
    spread_window_ns: int = Field(default=30_000_000_000, gt=0)
    markout_horizon_ns: int = Field(default=1_000_000_000, gt=0)
    warmup_samples: int = Field(default=20, ge=2)
    maximum_input_age_ns: int = Field(default=2_000_000_000, gt=0)
    low_volatility_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.5")
    high_volatility_bps: Annotated[Decimal, Field(gt=0)] = Decimal("8")
    inventory_limit_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.05")
    fill_model_id: Identifier = "heuristic-uncalibrated-v1"
    fill_model_calibrated: bool = False

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.low_volatility_bps >= self.high_volatility_bps:
            raise ValueError("low volatility threshold must be below high threshold")
        return self


class InventoryState(DomainModel):
    confirmed_base: Decimal = Decimal("0")
    target_base: Decimal = Decimal("0")
    liquidation_distance_bps: NonNegativeDecimal = Decimal("0")
    margin_utilization: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0")


class MicrostructureSnapshot(DomainModel):
    """One immutable, fully causal feature observation."""

    schema_version: Literal[1] = 1
    feature_set: Literal["btc-microstructure-v1"] = "btc-microstructure-v1"
    feature_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    sequence: int = Field(ge=0)
    event_ts_ns: int = Field(ge=0)
    receive_ts_ns: int = Field(ge=0)
    computed_ts_ns: int = Field(ge=0)
    max_input_age_ns: int = Field(ge=0)
    ready: bool
    warmup_count: int = Field(ge=0)

    best_bid: Annotated[Decimal, Field(gt=0)]
    best_ask: Annotated[Decimal, Field(gt=0)]
    midprice: Annotated[Decimal, Field(gt=0)]
    book_imbalance: UnitDecimal
    microprice: Annotated[Decimal, Field(gt=0)]
    vamp: Annotated[Decimal, Field(gt=0)]
    weighted_midprice: Annotated[Decimal, Field(gt=0)]
    queue_imbalance: UnitDecimal
    depth_imbalance: UnitDecimal

    trade_flow_imbalance: UnitDecimal
    buy_pressure: NonNegativeDecimal
    sell_pressure: NonNegativeDecimal
    aggressor_ratio: Annotated[Decimal, Field(ge=0, le=1)]
    volume_delta: Decimal
    signed_volume: Decimal

    realized_volatility: NonNegativeDecimal
    volatility_regime: VolatilityRegime
    atr_bps: NonNegativeDecimal
    spread_bps: NonNegativeDecimal
    spread_change_bps: Decimal
    spread_zscore: Decimal
    mid_return_bps: Decimal

    inventory_base: Decimal
    target_inventory_base: Decimal
    inventory_drift_base: Decimal
    inventory_risk: NonNegativeDecimal
    liquidation_distance_bps: NonNegativeDecimal
    margin_utilization: Annotated[Decimal, Field(ge=0, le=1)]

    fill_probability_bid: Annotated[Decimal, Field(ge=0, le=1)]
    fill_probability_ask: Annotated[Decimal, Field(ge=0, le=1)]
    queue_ahead_bid: NonNegativeDecimal
    queue_ahead_ask: NonNegativeDecimal
    adverse_selection_bps: Decimal
    fill_model_id: Identifier
    fill_model_calibrated: bool

    @model_validator(mode="after")
    def validate_causality(self) -> Self:
        if self.receive_ts_ns < self.event_ts_ns:
            raise ValueError("receive timestamp cannot precede source event time")
        if self.computed_ts_ns < self.receive_ts_ns:
            raise ValueError("computed timestamp cannot precede receipt time")
        if self.best_bid >= self.best_ask:
            raise ValueError("feature snapshot book must be uncrossed")
        if self.feature_schema_sha256 != MODEL_FEATURE_SCHEMA.sha256():
            raise ValueError("feature snapshot schema hash is not supported")
        return self

    def model_vector(self, schema: FeatureSchema = MODEL_FEATURE_SCHEMA) -> NDArray[np.float64]:
        if schema.sha256() != self.feature_schema_sha256:
            raise ValueError("feature schema does not match snapshot")
        values = [float(getattr(self, name)) for name in schema.names]
        vector = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(vector)):
            raise ValueError("model feature vector contains non-finite values")
        return vector
