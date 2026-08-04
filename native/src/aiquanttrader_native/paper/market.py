"""Causal assembly of normalized public events into the shared kernel state."""

from __future__ import annotations

from aiquanttrader_native.backtest.kernel import (
    KernelBookLevel,
    KernelMarketState,
    KernelTrade,
)
from aiquanttrader_native.domain.market import L2BookSnapshot, MarketEvent, TradeEvent


class LiveMarketStateAssembler:
    """Buffer trades until the next full L2 snapshot without inventing timestamps."""

    def __init__(self, *, depth_levels: int) -> None:
        if not 1 <= depth_levels <= 10:
            raise ValueError("live paper depth must be in [1, 10]")
        self._depth_levels = depth_levels
        self._pending_trades: list[TradeEvent] = []
        self._sequence = 0
        self._last_observed_ts_ns: int | None = None

    def observe(self, event: MarketEvent) -> KernelMarketState | None:
        if isinstance(event, TradeEvent):
            self._pending_trades.append(event)
            return None
        if not isinstance(event, L2BookSnapshot):
            return None
        observed = event.header.receive_ts_ns
        if self._last_observed_ts_ns is not None and observed <= self._last_observed_ts_ns:
            raise ValueError("live L2 receive timestamps must be strictly increasing")
        trades = tuple(
            KernelTrade(
                exchange_ts_ns=trade.header.event_ts_ns,
                observed_ts_ns=trade.header.receive_ts_ns,
                price=trade.price,
                size=trade.size,
                aggressor=trade.aggressor,
            )
            for trade in self._pending_trades
        )
        exchange_ts = max((event.header.event_ts_ns, *(trade.exchange_ts_ns for trade in trades)))
        if exchange_ts > observed:
            raise ValueError("live exchange timestamp follows local receipt; verify host clock")
        state = KernelMarketState(
            exchange_ts_ns=exchange_ts,
            book_exchange_ts_ns=event.header.event_ts_ns,
            observed_ts_ns=observed,
            sequence=self._sequence,
            bids=tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in event.bids[: self._depth_levels]
            ),
            asks=tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in event.asks[: self._depth_levels]
            ),
            trades=trades,
        )
        self._pending_trades.clear()
        self._sequence += 1
        self._last_observed_ts_ns = observed
        return state
