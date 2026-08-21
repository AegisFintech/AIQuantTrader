from __future__ import annotations

from decimal import Decimal

import pytest

from aiquanttrader.domain.market import (
    AggressorSide,
    BboEvent,
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


def bbo(
    event_ts: int,
    receive_ts: int,
    *,
    bid: str = "100",
    ask: str = "101",
) -> BboEvent:
    return BboEvent(
        header=header(f"bbo-{event_ts}", event_ts, receive_ts),
        bid_price=Decimal(bid),
        bid_size=Decimal("1.5"),
        ask_price=Decimal(ask),
        ask_size=Decimal("2.5"),
    )


def test_live_assembler_buffers_each_trade_once_until_next_book() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=1_000, minimum_state_interval_ns=1
    )
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
    assembler = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=1_000, minimum_state_interval_ns=1
    )
    assert assembler.observe(book(1_000, 1_010)) is not None
    with pytest.raises(ValueError, match="strictly increasing"):
        assembler.observe(book(1_000, 1_010))

    skewed = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=1_000, minimum_state_interval_ns=1
    )
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
        LiveMarketStateAssembler(
            depth_levels=0, maximum_input_age_ns=1, minimum_state_interval_ns=1
        )
    with pytest.raises(ValueError, match="input age"):
        LiveMarketStateAssembler(
            depth_levels=1, maximum_input_age_ns=0, minimum_state_interval_ns=1
        )
    with pytest.raises(ValueError, match="state interval"):
        LiveMarketStateAssembler(
            depth_levels=1, maximum_input_age_ns=1, minimum_state_interval_ns=0
        )


def test_live_assembler_excludes_stale_bootstrap_trades_before_features() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=100, minimum_state_interval_ns=1
    )
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


def test_live_assembler_excludes_stale_book_without_weakening_feature_age() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=100, minimum_state_interval_ns=1
    )

    assert assembler.observe(book(900, 1_010)) is None
    assert assembler.stale_book_exclusions == 1

    fresh = assembler.observe(book(1_020, 1_030))
    assert fresh is not None
    assert fresh.sequence == 0
    assert fresh.observed_ts_ns - fresh.book_exchange_ts_ns == 10


def test_live_assembler_emits_fresh_bbo_with_current_l2_depth() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=3, maximum_input_age_ns=100, minimum_state_interval_ns=1
    )
    depth = L2BookSnapshot(
        header=header("depth", 1_000, 1_010),
        bids=(
            BookLevel(price=Decimal("100"), size=Decimal("2")),
            BookLevel(price=Decimal("99"), size=Decimal("3")),
            BookLevel(price=Decimal("98"), size=Decimal("4")),
        ),
        asks=(
            BookLevel(price=Decimal("101"), size=Decimal("3")),
            BookLevel(price=Decimal("102"), size=Decimal("4")),
            BookLevel(price=Decimal("103"), size=Decimal("5")),
        ),
    )
    assert assembler.observe(depth) is not None

    state = assembler.observe(bbo(1_040, 1_050, bid="100.5", ask="100.75"))

    assert state is not None
    assert state.book_exchange_ts_ns == depth.header.event_ts_ns
    assert state.exchange_ts_ns == 1_040
    assert tuple(level.price for level in state.bids) == (
        Decimal("100.5"),
        Decimal("100"),
        Decimal("99"),
    )
    assert tuple(level.price for level in state.asks) == (
        Decimal("100.75"),
        Decimal("101"),
        Decimal("102"),
    )
    assert assembler.last_state_used_l2_depth
    assert assembler.latest_depth_receive_ts_ns == 1_010


def test_live_assembler_degrades_to_bbo_only_when_l2_depth_expires() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=3, maximum_input_age_ns=100, minimum_state_interval_ns=1
    )
    assert assembler.observe(book(1_000, 1_010)) is not None

    state = assembler.observe(bbo(1_200, 1_210, bid="100.25", ask="100.75"))

    assert state is not None
    assert state.book_exchange_ts_ns == 1_200
    assert len(state.bids) == len(state.asks) == 1
    assert not assembler.last_state_used_l2_depth


def test_live_assembler_excludes_stale_bbo_and_retains_pending_trades() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=1, maximum_input_age_ns=100, minimum_state_interval_ns=1
    )
    trade = TradeEvent(
        header=header("trade-before-stale-bbo", 950, 1_000),
        trade_id="trade-before-stale-bbo",
        price=Decimal("100"),
        size=Decimal("0.5"),
        aggressor=AggressorSide.BUYER,
    )
    assert assembler.observe(trade) is None
    assert assembler.observe(bbo(800, 1_000)) is None
    assert assembler.stale_bbo_exclusions == 1

    state = assembler.observe(bbo(1_010, 1_020))

    assert state is not None
    assert tuple(item.size for item in state.trades) == (Decimal("0.5"),)


def test_live_assembler_observes_every_bbo_but_emits_at_bounded_cadence() -> None:
    assembler = LiveMarketStateAssembler(
        depth_levels=1,
        maximum_input_age_ns=1_000,
        minimum_state_interval_ns=100,
    )
    assert assembler.observe(book(1_000, 1_010)) is not None
    trade = TradeEvent(
        header=header("buffered-trade", 1_040, 1_050),
        trade_id="buffered-trade",
        price=Decimal("100"),
        size=Decimal("0.25"),
        aggressor=AggressorSide.SELLER,
    )
    assert assembler.observe(trade) is None

    assert assembler.observe(bbo(1_050, 1_060)) is None
    assert assembler.latest_bbo_receive_ts_ns == 1_060
    emitted = assembler.observe(bbo(1_110, 1_120))

    assert emitted is not None
    assert emitted.sequence == 1
    assert tuple(item.size for item in emitted.trades) == (Decimal("0.25"),)
