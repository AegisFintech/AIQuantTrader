"""Causal assembly of normalized public events into the shared kernel state."""

from __future__ import annotations

from aiquanttrader.backtest.kernel import (
    KernelBookLevel,
    KernelMarketState,
    KernelTrade,
)
from aiquanttrader.domain.market import BboEvent, L2BookSnapshot, MarketEvent, TradeEvent


class LiveMarketStateAssembler:
    """Emit causal states from executable BBOs and independently fresh L2 depth."""

    def __init__(
        self,
        *,
        depth_levels: int,
        maximum_input_age_ns: int,
        minimum_state_interval_ns: int,
    ) -> None:
        if not 1 <= depth_levels <= 10:
            raise ValueError("live paper depth must be in [1, 10]")
        if maximum_input_age_ns <= 0:
            raise ValueError("live paper maximum input age must be positive")
        if minimum_state_interval_ns <= 0:
            raise ValueError("live paper minimum state interval must be positive")
        self._depth_levels = depth_levels
        self._maximum_input_age_ns = maximum_input_age_ns
        self._minimum_state_interval_ns = minimum_state_interval_ns
        self._pending_trades: list[TradeEvent] = []
        self._stale_trade_exclusions = 0
        self._stale_book_exclusions = 0
        self._stale_bbo_exclusions = 0
        self._sequence = 0
        self._last_observed_ts_ns: int | None = None
        self._last_book_receive_ts_ns: int | None = None
        self._latest_depth: L2BookSnapshot | None = None
        self._latest_bbo_receive_ts_ns: int | None = None
        self._last_state_used_l2_depth = False

    @property
    def stale_trade_exclusions(self) -> int:
        return self._stale_trade_exclusions

    @property
    def stale_book_exclusions(self) -> int:
        return self._stale_book_exclusions

    @property
    def stale_bbo_exclusions(self) -> int:
        return self._stale_bbo_exclusions

    @property
    def latest_depth_receive_ts_ns(self) -> int | None:
        return None if self._latest_depth is None else self._latest_depth.header.receive_ts_ns

    @property
    def latest_bbo_receive_ts_ns(self) -> int | None:
        return self._latest_bbo_receive_ts_ns

    @property
    def last_state_used_l2_depth(self) -> bool:
        return self._last_state_used_l2_depth

    def observe(self, event: MarketEvent) -> KernelMarketState | None:
        if isinstance(event, TradeEvent):
            observed = event.header.receive_ts_ns
            if event.header.event_ts_ns > observed:
                raise ValueError("live exchange timestamp follows local receipt; verify host clock")
            self._expire_stale_trades(observed)
            if observed - event.header.event_ts_ns > self._maximum_input_age_ns:
                self._stale_trade_exclusions += 1
            else:
                self._pending_trades.append(event)
            return None
        if not isinstance(event, (BboEvent, L2BookSnapshot)):
            return None
        observed = event.header.receive_ts_ns
        if self._last_book_receive_ts_ns is not None and observed <= self._last_book_receive_ts_ns:
            raise ValueError("live book receive timestamps must be strictly increasing")
        if event.header.event_ts_ns > observed:
            raise ValueError("live exchange timestamp follows local receipt; verify host clock")
        self._last_book_receive_ts_ns = observed
        self._expire_stale_trades(observed)
        if observed - event.header.event_ts_ns > self._maximum_input_age_ns:
            if isinstance(event, L2BookSnapshot):
                self._stale_book_exclusions += 1
            else:
                self._stale_bbo_exclusions += 1
            return None
        if isinstance(event, L2BookSnapshot):
            self._latest_depth = event
            if not self._state_due(observed):
                return None
            return self._state_from_l2(event)
        self._latest_bbo_receive_ts_ns = observed
        if not self._state_due(observed):
            return None
        return self._state_from_bbo(event)

    def _state_due(self, observed_ts_ns: int) -> bool:
        return (
            self._last_observed_ts_ns is None
            or observed_ts_ns - self._last_observed_ts_ns >= self._minimum_state_interval_ns
        )

    def _state_from_l2(self, event: L2BookSnapshot) -> KernelMarketState:
        return self._state(
            observed=event.header.receive_ts_ns,
            state_exchange_ts_ns=event.header.event_ts_ns,
            book_exchange_ts_ns=event.header.event_ts_ns,
            bids=tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in event.bids[: self._depth_levels]
            ),
            asks=tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in event.asks[: self._depth_levels]
            ),
            used_l2_depth=True,
        )

    def _state_from_bbo(self, event: BboEvent) -> KernelMarketState:
        depth = self._latest_depth
        depth_usable = (
            depth is not None
            and depth.header.event_ts_ns <= event.header.event_ts_ns
            and event.header.receive_ts_ns - depth.header.event_ts_ns <= self._maximum_input_age_ns
        )
        bid = KernelBookLevel(price=event.bid_price, size=event.bid_size)
        ask = KernelBookLevel(price=event.ask_price, size=event.ask_size)
        if depth_usable:
            assert depth is not None
            deeper_bids = tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in depth.bids
                if level.price < event.bid_price
            )[: self._depth_levels - 1]
            deeper_asks = tuple(
                KernelBookLevel(price=level.price, size=level.size)
                for level in depth.asks
                if level.price > event.ask_price
            )[: self._depth_levels - 1]
            bids = (bid, *deeper_bids)
            asks = (ask, *deeper_asks)
            book_exchange_ts_ns = depth.header.event_ts_ns
        else:
            bids = (bid,)
            asks = (ask,)
            book_exchange_ts_ns = event.header.event_ts_ns
        return self._state(
            observed=event.header.receive_ts_ns,
            state_exchange_ts_ns=event.header.event_ts_ns,
            book_exchange_ts_ns=book_exchange_ts_ns,
            bids=bids,
            asks=asks,
            used_l2_depth=depth_usable,
        )

    def _state(
        self,
        *,
        observed: int,
        state_exchange_ts_ns: int,
        book_exchange_ts_ns: int,
        bids: tuple[KernelBookLevel, ...],
        asks: tuple[KernelBookLevel, ...],
        used_l2_depth: bool,
    ) -> KernelMarketState:
        fresh_trades = tuple(self._pending_trades)
        trades = tuple(
            KernelTrade(
                exchange_ts_ns=trade.header.event_ts_ns,
                observed_ts_ns=trade.header.receive_ts_ns,
                price=trade.price,
                size=trade.size,
                aggressor=trade.aggressor,
            )
            for trade in fresh_trades
        )
        exchange_ts = max((state_exchange_ts_ns, *(trade.exchange_ts_ns for trade in trades)))
        if exchange_ts > observed:
            raise ValueError("live exchange timestamp follows local receipt; verify host clock")
        state = KernelMarketState(
            exchange_ts_ns=exchange_ts,
            book_exchange_ts_ns=book_exchange_ts_ns,
            observed_ts_ns=observed,
            sequence=self._sequence,
            bids=bids,
            asks=asks,
            trades=trades,
        )
        self._pending_trades.clear()
        self._sequence += 1
        self._last_observed_ts_ns = observed
        self._last_state_used_l2_depth = used_l2_depth
        return state

    def _expire_stale_trades(self, observed_ts_ns: int) -> None:
        cutoff = observed_ts_ns - self._maximum_input_age_ns
        fresh = [trade for trade in self._pending_trades if trade.header.event_ts_ns >= cutoff]
        self._stale_trade_exclusions += len(self._pending_trades) - len(fresh)
        self._pending_trades = fresh
