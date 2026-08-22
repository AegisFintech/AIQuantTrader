"""Typed, non-authoritative LLM confirmation evidence."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from aiquanttrader.domain.base import DomainModel
from aiquanttrader.features.market_structure import SmartMoneySnapshot
from aiquanttrader.features.models import VolatilityRegime


class LlmVerdict(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


class LlmAssessment(DomainModel):
    verdict: LlmVerdict
    confidence: Annotated[Decimal, Field(ge=0, le=1)]
    rationale: Annotated[str, Field(min_length=1, max_length=600)]
    invalidation_price: Annotated[Decimal, Field(gt=0)] | None = None
    expected_horizon_seconds: int = Field(ge=1, le=300)


class LlmConfirmationRequest(DomainModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    observed_ts_ns: int = Field(ge=0)
    strategy_id: Literal["smart-money-scalper-v3"] = "smart-money-scalper-v3"
    side: Literal["long", "short"]
    feature_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_price: Annotated[Decimal, Field(gt=0)]
    spread_bps: Annotated[Decimal, Field(ge=0)]
    book_imbalance: Annotated[Decimal, Field(ge=-1, le=1)]
    trade_flow_imbalance: Annotated[Decimal, Field(ge=-1, le=1)]
    volatility_regime: VolatilityRegime
    expected_edge_bps: Decimal
    required_edge_bps: Annotated[Decimal, Field(ge=0)]
    confluence_score: int = Field(ge=0, le=20)
    structure: SmartMoneySnapshot


class LlmConfirmation(DomainModel):
    schema_version: Literal[1] = 1
    confirmation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    completed_ts_ns: int = Field(ge=0)
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    latency_ms: Annotated[Decimal, Field(ge=0)]
    assessment: LlmAssessment
    authority: Literal["shadow_only_no_execution"] = "shadow_only_no_execution"
