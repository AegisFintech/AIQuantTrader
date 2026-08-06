from __future__ import annotations

from decimal import Decimal

import pytest

from aiquanttrader.domain.market import (
    AggressorSide,
    BookLevel,
    EventHeader,
    L2BookSnapshot,
    TradeEvent,
)
from aiquanttrader.paper.market import LiveMarketStateAssembler


def header(event_id: str, event_ts: int, receive_ts: int) -> EventHeader:
    return EventHeader(
        event_id=event_id,
        event_ts_ns=event_ts,
        receive_ts_ns=receive_ts,
        connection_id="paper-test",
    )


def book(event_ts: int, receive_ts: int) -> L2BookSnapshot:
    return L2BookSnapshot(
        header=header(f"book-{event_ts}", event_ts, receive_ts),
        bids=(BookLevel(price=Decimal("100"), size=Decimal("2")),),
        asks=(BookLevel(price=Decimal("101"), size=Decimal("3")),),
    )


def test_live_assembler_buffers_each_trade_once_until_next_book() -> None:
    assembler = LiveMarketStateAssembler(depth_levels=1, maximum_input_age_ns=1_000)
    trade = TradeEvent(
        header=header("trade-1", 1_000, 1_010),
        trade_id="trade-1",
        price=Decimal("100"),
        size=Decimal("0.5"),
        aggressor=AggressorSide.SELLER,
    )
    assert assembler.observe(trade) is None
    first = assembler.observe(book(1_020, 1_030))
    assert first is not None
    assert first.sequence == 0
    assert len(first.trades) == 1
    second = assembler.observe(book(1_040, 1_050))
    assert second is not None
    assert second.sequence == 1
    assert second.trades == ()


def test_live_assembler_rejects_nonmonotonic_receipt_and_clock_skew() -> None:
    assembler = LiveMarketStateAssembler(depth_levels=1, maximum_input_age_ns=1_000)
    assert assembler.observe(book(1_000, 1_010)) is not None
    with pytest.raises(ValueError, match="strictly increasing"):
        assembler.observe(book(1_000, 1_010))

    skewed = LiveMarketStateAssembler(depth_levels=1, maximum_input_age_ns=1_000)
    with pytest.raises(ValueError, match="host clock"):
        skewed.observe(book(2_000, 1_000))

    future_trade = TradeEvent(
        header=header("future-trade", 2_000, 1_000),
        trade_id="future-trade",
        price=Decimal("100"),
        size=Decimal("0.1"),
        aggressor=AggressorSide.BUYER,
    )
    with pytest.raises(ValueError, match="host clock"):
        skewed.observe(future_trade)


def test_live_assembler_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="depth"):
        LiveMarketStateAssembler(depth_levels=0, maximum_input_age_ns=1)
    with pytest.raises(ValueError, match="input age"):
        LiveMarketStateAssembler(depth_levels=1, maximum_input_age_ns=0)


def test_live_assembler_excludes_stale_bootstrap_trades_before_features() -> None:
    assembler = LiveMarketStateAssembler(depth_levels=1, maximum_input_age_ns=100)
    stale = TradeEvent(
        header=header("trade-stale", 100, 1_000),
        trade_id="trade-stale",
        price=Decimal("100"),
        size=Decimal("0.5"),
        aggressor=AggressorSide.SELLER,
    )
    fresh = TradeEvent(
        header=header("trade-fresh", 950, 1_000),
        trade_id="trade-fresh",
        price=Decimal("101"),
        size=Decimal("0.25"),
        aggressor=AggressorSide.BUYER,
    )

    assert assembler.observe(stale) is None
    assert assembler.observe(fresh) is None
    state = assembler.observe(book(990, 1_010))

    assert state is not None
    assert tuple(trade.size for trade in state.trades) == (Decimal("0.25"),)
    assert assembler.stale_trade_exclusions == 1
