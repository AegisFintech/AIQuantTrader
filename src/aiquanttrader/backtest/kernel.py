"""Pure kernel boundary and HftBacktest/Nautilus market-state adapters."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import groupby
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self, cast

import numpy as np
from pydantic import Field, model_validator

from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.execution import OrderIntent
from aiquanttrader.domain.market import AggressorSide

if TYPE_CHECKING:
    from nautilus_trader.model.data import OrderBookDepth10, QuoteTick, TradeTick

    type NautilusMarketData = QuoteTick | TradeTick | OrderBookDepth10
else:
    type NautilusMarketData = Any


class KernelBookLevel(DomainModel):
    price: Annotated[Decimal, Field(gt=0)]
    size: Annotated[Decimal, Field(gt=0)]


class KernelTrade(DomainModel):
    exchange_ts_ns: int = Field(ge=0)
    observed_ts_ns: int = Field(ge=0)
    price: Annotated[Decimal, Field(gt=0)]
    size: Annotated[Decimal, Field(gt=0)]
    aggressor: AggressorSide

    @model_validator(mode="after")
    def validate_causal_timestamp(self) -> Self:
        if self.observed_ts_ns < self.exchange_ts_ns:
            raise ValueError("kernel trade cannot be observed before its exchange event")
        return self


class KernelMarketState(DomainModel):
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    exchange_ts_ns: int = Field(ge=0)
    book_exchange_ts_ns: int = Field(ge=0)
    observed_ts_ns: int = Field(ge=0)
    sequence: int = Field(ge=0)
    bids: tuple[KernelBookLevel, ...] = Field(min_length=1)
    asks: tuple[KernelBookLevel, ...] = Field(min_length=1)
    trades: tuple[KernelTrade, ...] = ()

    @model_validator(mode="after")
    def validate_causal_book(self) -> Self:
        if self.observed_ts_ns < self.exchange_ts_ns:
            raise ValueError("kernel state cannot be observed before its exchange event")
        if self.book_exchange_ts_ns > self.exchange_ts_ns:
            raise ValueError("book timestamp cannot follow the latest state event")
        if any(trade.exchange_ts_ns > self.exchange_ts_ns for trade in self.trades):
            raise ValueError("trade timestamp cannot follow the latest state event")
        if any(trade.observed_ts_ns > self.observed_ts_ns for trade in self.trades):
            raise ValueError("trade receipt cannot follow the containing market state")
        if self.bids != tuple(sorted(self.bids, key=lambda level: level.price, reverse=True)):
            raise ValueError("kernel bids must be descending")
        if self.asks != tuple(sorted(self.asks, key=lambda level: level.price)):
            raise ValueError("kernel asks must be ascending")
        if self.bids[0].price >= self.asks[0].price:
            raise ValueError("kernel book cannot be locked or crossed")
        return self


class StrategyAction(StrEnum):
    WARMUP = "warmup"
    HOLD = "hold"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_TAKE_PROFIT = "exit_take_profit"
    EXIT_STOP_LOSS = "exit_stop_loss"
    EXIT_TIME_LIMIT = "exit_time_limit"
    EXIT_OPPOSITE_FLOW = "exit_opposite_flow"
    BLOCKED_COST = "blocked_cost"
    BLOCKED_SPREAD = "blocked_spread"
    BLOCKED_CONFLUENCE = "blocked_confluence"
    BLOCKED_COOLDOWN = "blocked_cooldown"
    BLOCKED_VOLATILITY = "blocked_volatility"
    BLOCKED_INVENTORY = "blocked_inventory"
    BLOCKED_MODEL = "blocked_model"


class KernelDecision(DomainModel):
    submit: tuple[OrderIntent, ...] = ()
    cancel_intent_ids: tuple[str, ...] = ()
    action: StrategyAction = StrategyAction.HOLD
    reason: str = "no_action"
    expected_edge_bps: Decimal = Decimal("0")
    required_edge_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    confluence_score: int = Field(default=0, ge=0, le=20)
    reference_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)
    position_age_seconds: Annotated[Decimal, Field(ge=0)] = Decimal("0")


@dataclass(frozen=True, slots=True)
class KernelTransition[MemoryT]:
    memory: MemoryT
    decision: KernelDecision


class DecisionKernel[MemoryT](Protocol):
    """A kernel cannot perform I/O; all mutable strategy state is explicit."""

    def decide(self, market: KernelMarketState, memory: MemoryT) -> KernelTransition[MemoryT]: ...


@dataclass(frozen=True, slots=True)
class KernelTrace[MemoryT]:
    final_memory: MemoryT
    decisions: tuple[KernelDecision, ...]


def replay_kernel[MemoryT](
    *,
    kernel: DecisionKernel[MemoryT],
    initial_memory: MemoryT,
    market_states: Iterable[KernelMarketState],
) -> KernelTrace[MemoryT]:
    memory = initial_memory
    decisions: list[KernelDecision] = []
    for market in market_states:
        transition = kernel.decide(market, memory)
        memory = transition.memory
        decisions.append(transition.decision)
    return KernelTrace(final_memory=memory, decisions=tuple(decisions))


def assert_kernel_parity(left: KernelTrace[Any], right: KernelTrace[Any]) -> None:
    left_decisions = [decision.model_dump(mode="json") for decision in left.decisions]
    right_decisions = [decision.model_dump(mode="json") for decision in right.decisions]
    if left_decisions != right_decisions or left.final_memory != right.final_memory:
        raise ValueError("HftBacktest and Nautilus kernel decision traces diverged")


def iter_hft_market_states(
    events: np.ndarray[Any, np.dtype[Any]], *, depth_levels: int = 10
) -> Iterator[KernelMarketState]:
    """Yield local-arrival states without retaining the complete replay in memory."""

    from hftbacktest import (
        BUY_EVENT,
        DEPTH_BBO_EVENT,
        DEPTH_CLEAR_EVENT,
        DEPTH_EVENT,
        DEPTH_SNAPSHOT_EVENT,
        LOCAL_EVENT,
        SELL_EVENT,
        TRADE_EVENT,
        event_dtype,
    )

    if events.dtype != event_dtype:
        raise ValueError("events do not use the pinned HftBacktest dtype")
    if depth_levels < 1:
        raise ValueError("depth_levels must be positive")
    local_rows = (row for row in events if int(row["ev"]) & LOCAL_EVENT)
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    bid_exchange_ts_ns: int | None = None
    ask_exchange_ts_ns: int | None = None
    for sequence, (local_ts, grouped) in enumerate(
        groupby(local_rows, key=lambda row: int(row["local_ts"]))
    ):
        exchange_ts = 0
        trades: list[KernelTrade] = []
        for row in grouped:
            flags = int(row["ev"])
            event_kind = flags & 0xFF
            exchange_ts = max(exchange_ts, int(row["exch_ts"]))
            price = float(row["px"])
            quantity = float(row["qty"])
            if event_kind in {
                DEPTH_EVENT,
                DEPTH_CLEAR_EVENT,
                DEPTH_SNAPSHOT_EVENT,
                DEPTH_BBO_EVENT,
            }:
                side = bids if flags & BUY_EVENT else asks
                if flags & BUY_EVENT:
                    bid_exchange_ts_ns = int(row["exch_ts"])
                else:
                    ask_exchange_ts_ns = int(row["exch_ts"])
                if event_kind == DEPTH_CLEAR_EVENT:
                    discarded = (
                        (level for level in side if level >= price)
                        if flags & BUY_EVENT
                        else (level for level in side if level <= price)
                    )
                    for level in tuple(discarded):
                        side.pop(level, None)
                elif event_kind == DEPTH_BBO_EVENT:
                    stale_better = (
                        (level for level in side if level > price)
                        if flags & BUY_EVENT
                        else (level for level in side if level < price)
                    )
                    for level in tuple(stale_better):
                        side.pop(level, None)
                    if quantity == 0:
                        side.pop(price, None)
                    else:
                        side[price] = quantity
                elif quantity == 0:
                    side.pop(price, None)
                else:
                    side[price] = quantity
            elif event_kind == TRADE_EVENT:
                aggressor = (
                    AggressorSide.BUYER
                    if flags & BUY_EVENT
                    else AggressorSide.SELLER
                    if flags & SELL_EVENT
                    else AggressorSide.UNKNOWN
                )
                trades.append(
                    KernelTrade(
                        exchange_ts_ns=int(row["exch_ts"]),
                        observed_ts_ns=local_ts,
                        price=Decimal(str(price)),
                        size=Decimal(str(quantity)),
                        aggressor=aggressor,
                    )
                )
        if not bids or not asks or bid_exchange_ts_ns is None or ask_exchange_ts_ns is None:
            continue
        bid_levels = tuple(
            KernelBookLevel(price=Decimal(str(price)), size=Decimal(str(size)))
            for price, size in sorted(bids.items(), reverse=True)[:depth_levels]
        )
        ask_levels = tuple(
            KernelBookLevel(price=Decimal(str(price)), size=Decimal(str(size)))
            for price, size in sorted(asks.items())[:depth_levels]
        )
        if bid_levels[0].price >= ask_levels[0].price:
            raise ValueError(
                "HftBacktest replay produced a crossed local book "
                f"at {local_ts}: bid={bid_levels[0].price} ask={ask_levels[0].price}"
            )
        yield KernelMarketState(
            exchange_ts_ns=max(exchange_ts, bid_exchange_ts_ns, ask_exchange_ts_ns),
            book_exchange_ts_ns=min(bid_exchange_ts_ns, ask_exchange_ts_ns),
            observed_ts_ns=local_ts,
            sequence=sequence,
            bids=bid_levels,
            asks=ask_levels,
            trades=tuple(trades),
        )


def hft_market_states(
    events: np.ndarray[Any, np.dtype[Any]], *, depth_levels: int = 10
) -> tuple[KernelMarketState, ...]:
    """Materialize local-arrival states for bounded tests and parity checks."""

    return tuple(iter_hft_market_states(events, depth_levels=depth_levels))


def nautilus_market_states(
    events: Sequence[NautilusMarketData], *, depth_levels: int = 10
) -> tuple[KernelMarketState, ...]:
    """Normalize actual Nautilus objects to the identical kernel contract."""

    from nautilus_trader.model.data import OrderBookDepth10, QuoteTick, TradeTick
    from nautilus_trader.model.enums import AggressorSide as NautilusAggressorSide

    if depth_levels < 1 or depth_levels > 10:
        raise ValueError("Nautilus depth_levels must be in [1, 10]")
    ordered = sorted(events, key=lambda event: (int(event.ts_init), int(event.ts_event)))
    bids: tuple[KernelBookLevel, ...] = ()
    asks: tuple[KernelBookLevel, ...] = ()
    book_exchange_ts_ns: int | None = None
    states: list[KernelMarketState] = []
    for sequence, (local_ts, grouped) in enumerate(
        groupby(ordered, key=lambda event: int(event.ts_init))
    ):
        exchange_ts = 0
        trades: list[KernelTrade] = []
        for event in grouped:
            if str(event.instrument_id) != "BTC-USD-PERP.HYPERLIQUID":
                raise ValueError("Nautilus replay received an unexpected instrument")
            exchange_ts = max(exchange_ts, int(event.ts_event))
            if isinstance(event, OrderBookDepth10):
                bids = tuple(
                    KernelBookLevel(price=Decimal(str(order.price)), size=Decimal(str(order.size)))
                    for order in event.bids[:depth_levels]
                )
                asks = tuple(
                    KernelBookLevel(price=Decimal(str(order.price)), size=Decimal(str(order.size)))
                    for order in event.asks[:depth_levels]
                )
                book_exchange_ts_ns = int(event.ts_event)
            elif isinstance(event, QuoteTick):
                bids = (
                    KernelBookLevel(
                        price=Decimal(str(event.bid_price)), size=Decimal(str(event.bid_size))
                    ),
                )
                asks = (
                    KernelBookLevel(
                        price=Decimal(str(event.ask_price)), size=Decimal(str(event.ask_size))
                    ),
                )
                book_exchange_ts_ns = int(event.ts_event)
            else:
                trade = cast(TradeTick, event)
                aggressor = {
                    NautilusAggressorSide.BUYER: AggressorSide.BUYER,
                    NautilusAggressorSide.SELLER: AggressorSide.SELLER,
                }.get(trade.aggressor_side, AggressorSide.UNKNOWN)
                trades.append(
                    KernelTrade(
                        exchange_ts_ns=int(trade.ts_event),
                        observed_ts_ns=local_ts,
                        price=Decimal(str(trade.price)),
                        size=Decimal(str(trade.size)),
                        aggressor=aggressor,
                    )
                )
        if not bids or not asks or book_exchange_ts_ns is None:
            continue
        if bids[0].price >= asks[0].price:
            raise ValueError("Nautilus replay produced a crossed local book")
        states.append(
            KernelMarketState(
                exchange_ts_ns=max(exchange_ts, book_exchange_ts_ns),
                book_exchange_ts_ns=book_exchange_ts_ns,
                observed_ts_ns=local_ts,
                sequence=sequence,
                bids=bids,
                asks=asks,
                trades=tuple(trades),
            )
        )
    return tuple(states)
