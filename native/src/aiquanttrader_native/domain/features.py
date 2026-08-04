"""Causal feature snapshot schema shared by research and live execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from aiquanttrader_native.domain.base import DomainModel


class FeatureSnapshot(DomainModel):
    schema_version: Literal[1] = 1
    feature_set: Annotated[str, Field(min_length=1, max_length=128)]
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    event_ts_ns: int = Field(ge=0)
    receive_ts_ns: int = Field(ge=0)
    computed_ts_ns: int = Field(ge=0)
    max_input_age_ns: int = Field(ge=0)
    book_imbalance: Decimal = Field(ge=-1, le=1)
    microprice: Decimal = Field(gt=0)
    vamp: Decimal = Field(gt=0)
    weighted_midprice: Decimal = Field(gt=0)
    depth_imbalance: Decimal = Field(ge=-1, le=1)
    trade_flow_imbalance: Decimal = Field(ge=-1, le=1)
    aggressor_ratio: Decimal = Field(ge=0, le=1)
    volume_delta: Decimal
    realized_volatility: Decimal = Field(ge=0)
    atr: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    inventory_base: Decimal
    inventory_notional_usd: Decimal
    fill_probability: Decimal = Field(ge=0, le=1)
    adverse_selection_bps: Decimal

    @model_validator(mode="after")
    def timestamps_are_causal(self) -> FeatureSnapshot:
        if self.receive_ts_ns < self.event_ts_ns:
            raise ValueError("receive timestamp must not precede source event timestamp")
        if self.computed_ts_ns < self.receive_ts_ns:
            raise ValueError("computed timestamp must not precede receive timestamp")
        return self
