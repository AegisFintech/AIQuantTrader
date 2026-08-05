from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import numpy as np
import pytest
from hftbacktest import BUY_EVENT, DEPTH_EVENT, SELL_EVENT, TRADE_EVENT, event_dtype
from hftbacktest.data.validation import correct_event_order
from nautilus_trader.model import Price, Quantity
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide as NautilusAggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from pydantic import ValidationError

from aiquanttrader.backtest.kernel import (
    KernelBookLevel,
    KernelDecision,
    KernelMarketState,
    KernelTrade,
    KernelTransition,
    assert_kernel_parity,
    hft_market_states,
    nautilus_market_states,
    replay_kernel,
)
from aiquanttrader.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader.domain.market import AggressorSide, OrderSide


class ImbalanceProbeKernel:
    """Deterministic test kernel; it is not an alpha candidate."""

    def decide(self, market: KernelMarketState, memory: int) -> KernelTransition[int]:
        next_memory = memory + 1
        if market.bids[0].size >= market.asks[0].size:
            return KernelTransition(memory=next_memory, decision=KernelDecision())
        intent = OrderIntent(
            intent_id=f"parity-{market.sequence}",
            strategy_id="parity-probe",
            side=OrderSide.SELL,
            kind=OrderKind.LIMIT,
            quantity_base=Decimal("0.001"),
            limit_price=market.asks[0].price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
            created_ts_ns=market.observed_ts_ns,
            rationale="representation parity probe",
        )
        return KernelTransition(
            memory=next_memory,
            decision=KernelDecision(submit=(intent,)),
        )


def hft_events() -> np.ndarray[Any, np.dtype[Any]]:
    rows = [
        (DEPTH_EVENT | BUY_EVENT, 1_000, 1_100, 100.0, 5.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 1_000, 1_100, 101.0, 5.0, 0, 0, 0.0),
        (TRADE_EVENT | SELL_EVENT, 2_000, 2_100, 100.0, 3.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 2_000, 2_100, 100.0, 2.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 2_000, 2_100, 101.0, 5.0, 0, 0, 0.0),
    ]
    raw = np.asarray(rows, dtype=event_dtype)
    return cast(
        np.ndarray[Any, np.dtype[Any]],
        correct_event_order(
            raw,
            np.argsort(raw["exch_ts"], kind="mergesort"),
            np.argsort(raw["local_ts"], kind="mergesort"),
        ),
    )


def nautilus_events() -> list[QuoteTick | TradeTick]:
    instrument = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
    return [
        QuoteTick(
            instrument_id=instrument,
            bid_price=Price.from_str("100.0"),
            ask_price=Price.from_str("101.0"),
            bid_size=Quantity.from_str("5.0"),
            ask_size=Quantity.from_str("5.0"),
            ts_event=1_000,
            ts_init=1_100,
        ),
        TradeTick(
            instrument_id=instrument,
            price=Price.from_str("100.0"),
            size=Quantity.from_str("3.0"),
            aggressor_side=NautilusAggressorSide.SELLER,
            trade_id=TradeId("trade-1"),
            ts_event=2_000,
            ts_init=2_100,
        ),
        QuoteTick(
            instrument_id=instrument,
            bid_price=Price.from_str("100.0"),
            ask_price=Price.from_str("101.0"),
            bid_size=Quantity.from_str("2.0"),
            ask_size=Quantity.from_str("5.0"),
            ts_event=2_000,
            ts_init=2_100,
        ),
    ]


def test_shared_kernel_decisions_match_hft_and_real_nautilus_objects() -> None:
    hft_states = hft_market_states(hft_events(), depth_levels=1)
    nautilus_states = nautilus_market_states(nautilus_events(), depth_levels=1)
    assert hft_states == nautilus_states

    kernel = ImbalanceProbeKernel()
    hft_trace = replay_kernel(kernel=kernel, initial_memory=0, market_states=hft_states)
    nautilus_trace = replay_kernel(
        kernel=kernel,
        initial_memory=0,
        market_states=nautilus_states,
    )
    assert_kernel_parity(hft_trace, nautilus_trace)
    assert hft_trace.final_memory == 2
    assert hft_trace.decisions[1].submit[0].intent_id == "parity-1"

    divergent = replay_kernel(
        kernel=kernel,
        initial_memory=0,
        market_states=nautilus_states[:1],
    )
    with pytest.raises(ValueError, match="diverged"):
        assert_kernel_parity(hft_trace, divergent)


def test_hft_adapter_ignores_exchange_events_until_local_arrival() -> None:
    rows = [
        (DEPTH_EVENT | BUY_EVENT, 1_000, 1_100, 100.0, 5.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 1_000, 1_100, 101.0, 5.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 1_500, 5_000, 99.0, 9.0, 0, 0, 0.0),
        (TRADE_EVENT | SELL_EVENT, 2_000, 2_100, 100.0, 1.0, 0, 0, 0.0),
    ]
    raw = np.asarray(rows, dtype=event_dtype)
    events = correct_event_order(
        raw,
        np.argsort(raw["exch_ts"], kind="mergesort"),
        np.argsort(raw["local_ts"], kind="mergesort"),
    )
    states = hft_market_states(events, depth_levels=2)

    state_at_2_100 = next(state for state in states if state.observed_ts_ns == 2_100)
    assert [level.price for level in state_at_2_100.bids] == [Decimal("100.0")]
    final = next(state for state in states if state.observed_ts_ns == 5_000)
    assert [level.price for level in final.bids] == [Decimal("100.0"), Decimal("99.0")]


def test_kernel_adapters_and_contract_reject_invalid_market_state() -> None:
    with pytest.raises(ValueError, match="pinned"):
        hft_market_states(np.zeros(1), depth_levels=1)
    with pytest.raises(ValueError, match="positive"):
        hft_market_states(hft_events(), depth_levels=0)
    with pytest.raises(ValueError, match=r"\[1, 10\]"):
        nautilus_market_states([], depth_levels=0)
    with pytest.raises(ValidationError, match="observed before"):
        KernelMarketState(
            exchange_ts_ns=2,
            book_exchange_ts_ns=2,
            observed_ts_ns=1,
            sequence=0,
            bids=(KernelBookLevel(price=Decimal("100"), size=Decimal("1")),),
            asks=(KernelBookLevel(price=Decimal("101"), size=Decimal("1")),),
        )
    with pytest.raises(ValidationError, match="descending"):
        KernelMarketState(
            exchange_ts_ns=1,
            book_exchange_ts_ns=1,
            observed_ts_ns=2,
            sequence=0,
            bids=(
                KernelBookLevel(price=Decimal("99"), size=Decimal("1")),
                KernelBookLevel(price=Decimal("100"), size=Decimal("1")),
            ),
            asks=(KernelBookLevel(price=Decimal("101"), size=Decimal("1")),),
        )
    with pytest.raises(ValidationError, match="trade timestamp"):
        KernelMarketState(
            exchange_ts_ns=1,
            book_exchange_ts_ns=1,
            observed_ts_ns=2,
            sequence=0,
            bids=(KernelBookLevel(price=Decimal("100"), size=Decimal("1")),),
            asks=(KernelBookLevel(price=Decimal("101"), size=Decimal("1")),),
            trades=(
                KernelTrade(
                    exchange_ts_ns=2,
                    observed_ts_ns=2,
                    price=Decimal("100"),
                    size=Decimal("1"),
                    aggressor=AggressorSide.SELLER,
                ),
            ),
        )
