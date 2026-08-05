"""Versioned execution and risk-decision contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.market import OrderSide

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
TimestampNs = Annotated[int, Field(ge=0)]


class OrderKind(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(StrEnum):
    GTC = "gtc"
    IOC = "ioc"


class OrderIntent(DomainModel):
    schema_version: Literal[1] = 1
    intent_id: Identifier
    strategy_id: Identifier
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    side: OrderSide
    kind: OrderKind
    quantity_base: PositiveDecimal
    limit_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    reduce_only: bool = False
    created_ts_ns: TimestampNs
    rationale: Annotated[str, Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def validate_execution_instructions(self) -> OrderIntent:
        if self.kind is OrderKind.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require a limit price")
        if self.kind is OrderKind.MARKET and self.limit_price is not None:
            raise ValueError("market orders cannot carry a limit price")
        if self.post_only and self.kind is not OrderKind.LIMIT:
            raise ValueError("post-only is valid only for limit orders")
        if self.post_only and self.time_in_force is not TimeInForce.GTC:
            raise ValueError("post-only orders require GTC time in force")
        if self.kind is OrderKind.MARKET and self.time_in_force is not TimeInForce.IOC:
            raise ValueError("market orders require IOC time in force")
        return self


class RiskState(StrEnum):
    ACTIVE = "active"
    REDUCE_ONLY = "reduce_only"
    CANCEL_ONLY = "cancel_only"
    HALTED = "halted"
    FLATTENING = "flattening"


class RiskReason(StrEnum):
    APPROVED = "approved"
    OPERATOR_KILL = "operator_kill"
    FLATTEN_REQUESTED = "flatten_requested"
    EXCHANGE_DISCONNECTED = "exchange_disconnected"
    RECONCILIATION_INCOMPLETE = "reconciliation_incomplete"
    PUBLIC_DATA_STALE = "public_data_stale"
    PRIVATE_DATA_STALE = "private_data_stale"
    INVALID_ACCOUNT_STATE = "invalid_account_state"
    DEPLOYMENT_APPROVAL_INVALID = "deployment_approval_invalid"
    CAPITAL_LIMIT = "capital_limit"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    DRAWDOWN_LIMIT = "drawdown_limit"
    ORDER_SIZE_LIMIT = "order_size_limit"
    ORDER_NOTIONAL_LIMIT = "order_notional_limit"
    POSITION_LIMIT = "position_limit"
    INVENTORY_LIMIT = "inventory_limit"
    LEVERAGE_LIMIT = "leverage_limit"
    OPEN_ORDER_LIMIT = "open_order_limit"
    INFLIGHT_REQUEST_LIMIT = "inflight_request_limit"
    ORDER_RATE_LIMIT = "order_rate_limit"
    REDUCE_ONLY_REQUIRED = "reduce_only_required"
    NOT_POSITION_REDUCING = "not_position_reducing"
    INTENT_TOO_OLD = "intent_too_old"
    INVALID_TIMESTAMP = "invalid_timestamp"


class RiskSnapshot(DomainModel):
    schema_version: Literal[1] = 1
    snapshot_ts_ns: TimestampNs
    public_data_ts_ns: TimestampNs
    private_data_ts_ns: TimestampNs
    mark_price: PositiveDecimal
    position_base: Decimal
    pending_buy_base: NonNegativeDecimal = Decimal("0")
    pending_sell_base: NonNegativeDecimal = Decimal("0")
    account_equity_usd: PositiveDecimal
    day_start_equity_usd: PositiveDecimal
    high_water_equity_usd: PositiveDecimal
    leverage: NonNegativeDecimal
    open_order_count: int = Field(ge=0)
    exchange_connected: bool
    reconciliation_complete: bool
    deployment_approved: bool = True
    approved_capital_limit_usd: PositiveDecimal | None = None
    operator_kill: bool = False
    flatten_requested: bool = False

    @model_validator(mode="after")
    def source_times_cannot_follow_snapshot(self) -> RiskSnapshot:
        if self.public_data_ts_ns > self.snapshot_ts_ns:
            raise ValueError("public data time cannot follow snapshot time")
        if self.private_data_ts_ns > self.snapshot_ts_ns:
            raise ValueError("private data time cannot follow snapshot time")
        return self


class RiskDecision(DomainModel):
    schema_version: Literal[1] = 1
    decision_id: Identifier
    intent_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    limits_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    state: RiskState
    allowed: bool
    reasons: tuple[RiskReason, ...] = Field(min_length=1)
    issued_ts_ns: TimestampNs
    expires_ts_ns: TimestampNs
    approval_signature: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> RiskDecision:
        if self.expires_ts_ns < self.issued_ts_ns:
            raise ValueError("risk decision expiry precedes issue time")
        if self.allowed and self.approval_signature is None:
            raise ValueError("allowed decisions require an approval signature")
        if not self.allowed and self.approval_signature is not None:
            raise ValueError("denied decisions cannot carry an approval signature")
        return self


class ExecutionState(StrEnum):
    PENDING_SUBMIT = "pending_submit"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_MODIFY = "pending_modify"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    REJECTED = "rejected"
    DENIED = "denied"
    UNKNOWN = "unknown"


class ExecutionJournalEvent(DomainModel):
    schema_version: Literal[1] = 1
    event_id: Identifier
    intent_id: Identifier
    client_order_id: Identifier | None = None
    venue_order_id: Identifier | None = None
    state: ExecutionState
    event_ts_ns: TimestampNs
    filled_quantity_base: NonNegativeDecimal = Decimal("0")
    detail: Annotated[str, Field(min_length=1, max_length=1_024)]
    source: Literal["risk", "nautilus", "reconciliation", "operator"]


class TradingHeartbeat(DomainModel):
    schema_version: Literal[1] = 1
    process_id: int = Field(gt=0)
    heartbeat_ts_ns: TimestampNs
    environment: Identifier
    account_address: Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]
    execution_healthy: bool
    reconciliation_complete: bool
    operator_kill: bool
    config_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    deployment_id: Identifier | None = None
    approval_id: Identifier | None = None
    admission_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    approval_expires_ts_ns: TimestampNs | None = None

    @model_validator(mode="after")
    def admission_identity_is_complete(self) -> TradingHeartbeat:
        identity = (
            self.deployment_id,
            self.approval_id,
            self.admission_id,
            self.approval_expires_ts_ns,
        )
        if any(value is not None for value in identity) and not all(
            value is not None for value in identity
        ):
            raise ValueError("heartbeat deployment admission identity must be complete")
        return self
