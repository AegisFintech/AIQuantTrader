"""Versioned normalized Hyperliquid market and account events."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from aiquanttrader_native.domain.base import DomainModel

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
TimestampNs = Annotated[int, Field(ge=0)]


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AggressorSide(StrEnum):
    BUYER = "buyer"
    SELLER = "seller"
    UNKNOWN = "unknown"


class EventHeader(DomainModel):
    schema_version: Literal[1] = 1
    event_id: Annotated[str, Field(min_length=1, max_length=256)]
    venue: Literal["HYPERLIQUID"] = "HYPERLIQUID"
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    event_ts_ns: TimestampNs
    receive_ts_ns: TimestampNs
    connection_id: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def receive_time_cannot_precede_exchange_time_unreasonably(self) -> EventHeader:
        if self.receive_ts_ns + 60_000_000_000 < self.event_ts_ns:
            raise ValueError("receive timestamp precedes event timestamp by more than 60 seconds")
        return self


class BookLevel(DomainModel):
    price: PositiveDecimal
    size: PositiveDecimal
    order_count: int = Field(ge=1)


class L2BookSnapshot(DomainModel):
    event_type: Literal["l2_book"] = "l2_book"
    header: EventHeader
    bids: tuple[BookLevel, ...] = Field(min_length=1)
    asks: tuple[BookLevel, ...] = Field(min_length=1)
    is_snapshot: Literal[True] = True

    @model_validator(mode="after")
    def validate_book(self) -> L2BookSnapshot:
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if len(set(bid_prices)) != len(bid_prices) or len(set(ask_prices)) != len(ask_prices):
            raise ValueError("book levels must have unique prices on each side")
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted from highest to lowest")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted from lowest to highest")
        if bid_prices[0] >= ask_prices[0]:
            raise ValueError("book must not be locked or crossed")
        return self


class BboEvent(DomainModel):
    event_type: Literal["bbo"] = "bbo"
    header: EventHeader
    bid_price: PositiveDecimal
    bid_size: PositiveDecimal
    ask_price: PositiveDecimal
    ask_size: PositiveDecimal

    @model_validator(mode="after")
    def validate_spread(self) -> BboEvent:
        if self.bid_price >= self.ask_price:
            raise ValueError("best bid must be below best ask")
        return self


class TradeEvent(DomainModel):
    event_type: Literal["trade"] = "trade"
    header: EventHeader
    trade_id: Annotated[str, Field(min_length=1, max_length=256)]
    price: PositiveDecimal
    size: PositiveDecimal
    aggressor: AggressorSide
    transaction_hash: str | None = None


class FundingEvent(DomainModel):
    event_type: Literal["funding"] = "funding"
    header: EventHeader
    funding_rate: Decimal
    next_funding_ts_ns: TimestampNs | None = None


class OpenInterestEvent(DomainModel):
    event_type: Literal["open_interest"] = "open_interest"
    header: EventHeader
    open_interest_base: NonNegativeDecimal


class MarkPriceEvent(DomainModel):
    event_type: Literal["mark_price"] = "mark_price"
    header: EventHeader
    mark_price: PositiveDecimal


class IndexPriceEvent(DomainModel):
    event_type: Literal["index_price"] = "index_price"
    header: EventHeader
    index_price: PositiveDecimal


class AccountLiquidationEvent(DomainModel):
    event_type: Literal["account_liquidation"] = "account_liquidation"
    header: EventHeader
    side: OrderSide
    price: PositiveDecimal
    size: PositiveDecimal
    liquidation_id: Annotated[str, Field(min_length=1, max_length=256)]
    is_adl: bool = False


class DataCapabilities(DomainModel):
    schema_version: Literal[1] = 1
    market_wide_liquidations_available: Literal[False] = False
    account_liquidations_available: bool = True


type MarketEvent = (
    L2BookSnapshot
    | BboEvent
    | TradeEvent
    | FundingEvent
    | OpenInterestEvent
    | MarkPriceEvent
    | IndexPriceEvent
    | AccountLiquidationEvent
)
