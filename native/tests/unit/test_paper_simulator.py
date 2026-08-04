from __future__ import annotations

from decimal import Decimal

import pytest

from aiquanttrader_native.backtest.kernel import KernelBookLevel, KernelMarketState, KernelTrade
from aiquanttrader_native.backtest.models import CalibrationState, ExecutionScenario, QueueModel
from aiquanttrader_native.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader_native.domain.market import AggressorSide, OrderSide
from aiquanttrader_native.paper.models import PaperOrderState
from aiquanttrader_native.paper.simulator import PaperExchangeSimulator


def scenario(**updates: object) -> ExecutionScenario:
    values: dict[str, object] = {
        "scenario_id": "paper-test-v1",
        "calibration_state": CalibrationState.UNCALIBRATED,
        "tick_size": "1",
        "lot_size": "0.001",
        "entry_latency_ns": 10,
        "response_latency_ns": 20,
        "feed_latency_offset_ns": 0,
        "maker_fee_bps": "1",
        "taker_fee_bps": "5",
        "queue_model": QueueModel.RISK_ADVERSE,
        "allow_partial_fills": True,
        "book_liquidity_multiplier": "1",
        "trade_flow_multiplier": "1",
        "taker_slippage_bps": "1",
        "funding_rate_multiplier": "1",
    }
    values.update(updates)
    return ExecutionScenario.model_validate(values)


def market(sequence: int, *, trades: tuple[KernelTrade, ...] = ()) -> KernelMarketState:
    timestamp = 1_000 + sequence * 100
    return KernelMarketState(
        exchange_ts_ns=timestamp,
        book_exchange_ts_ns=timestamp,
        observed_ts_ns=timestamp + 50,
        sequence=sequence,
        bids=(KernelBookLevel(price=Decimal("100"), size=Decimal("1")),),
        asks=(KernelBookLevel(price=Decimal("101"), size=Decimal("1")),),
        trades=trades,
    )


def intent(
    intent_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    kind: OrderKind = OrderKind.LIMIT,
    limit_price: Decimal | None = Decimal("100"),
    post_only: bool = True,
) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        strategy_id="paper-unit",
        side=side,
        kind=kind,
        quantity_base=Decimal("0.001"),
        limit_price=limit_price,
        time_in_force=TimeInForce.GTC if kind is OrderKind.LIMIT else TimeInForce.IOC,
        post_only=post_only,
        created_ts_ns=1_000,
        rationale="paper simulator unit test",
    )


def test_market_order_has_latency_adverse_rounding_fees_and_reconciled_equity() -> None:
    simulator = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    order = simulator.submit(
        intent(
            "market-buy",
            kind=OrderKind.MARKET,
            limit_price=None,
            post_only=False,
        ),
        accepted_ts_ns=1_000,
    )
    assert order.state is PaperOrderState.PENDING_ACTIVATION

    update = simulator.advance(market(1))
    assert len(update.fills) == 1
    fill = update.fills[0]
    assert fill.price == Decimal("102")
    assert fill.fee_usd == Decimal("0.000051")
    assert update.account.position_base == Decimal("0.001")
    assert update.account.cash_usd == Decimal("999.897949")
    assert update.account.equity_usd == Decimal("999.998449")
    assert update.orders[0].state is PaperOrderState.FILLED


def test_resting_order_waits_behind_public_queue_then_partially_fills() -> None:
    simulator = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    simulator.submit(intent("passive-buy"), accepted_ts_ns=1_000)
    activated = simulator.advance(market(1))
    assert activated.orders[0].state is PaperOrderState.RESTING
    assert activated.orders[0].queue_ahead_base == Decimal("1")

    first_trade = KernelTrade(
        exchange_ts_ns=1_200,
        observed_ts_ns=1_250,
        price=Decimal("100"),
        size=Decimal("0.6"),
        aggressor=AggressorSide.SELLER,
    )
    first = simulator.advance(market(2, trades=(first_trade,)))
    assert first.fills == ()
    assert first.orders[0].queue_ahead_base == Decimal("0.4")

    second_trade = first_trade.model_copy(update={"exchange_ts_ns": 1_300, "observed_ts_ns": 1_350})
    second = simulator.advance(market(3, trades=(second_trade,)))
    assert second.fills[0].quantity_base == Decimal("0.001")
    assert second.fills[0].maker
    assert second.orders[0].state is PaperOrderState.FILLED


def test_post_only_reject_cancel_latency_and_funding_are_explicit() -> None:
    simulator = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    crossing = intent("crossing", limit_price=Decimal("101"))
    simulator.submit(crossing, accepted_ts_ns=1_000)
    rejected = simulator.advance(market(1)).orders[0]
    assert rejected.state is PaperOrderState.REJECTED
    assert rejected.rejection_reason == "post_only_would_cross"

    simulator.submit(intent("cancel-me"), accepted_ts_ns=1_200)
    simulator.advance(market(3))
    pending = simulator.request_cancel("cancel-me", requested_ts_ns=1_360)
    assert pending is not None and pending.state is PaperOrderState.PENDING_CANCEL
    assert simulator.elapse(1_379) == ()
    assert simulator.elapse(1_380)[0].state is PaperOrderState.CANCELED

    account = simulator.settle_funding(
        funding_rate=Decimal("0.0001"),
        mark_price=Decimal("100"),
        settlement_ts_ns=3_600_000_000_000,
    )
    assert account.funding_pnl_usd == 0
    assert account.last_funding_settlement_ns == 3_600_000_000_000


def test_simulator_rejects_invalid_configuration_and_duplicate_identities() -> None:
    with pytest.raises(ValueError, match="positive capital"):
        PaperExchangeSimulator(
            scenario(),
            initial_equity_usd=Decimal("0"),
            initial_mark_price=Decimal("100"),
            started_ts_ns=1_000,
        )
    with pytest.raises(ValueError, match="identity namespace"):
        PaperExchangeSimulator(
            scenario(),
            initial_equity_usd=Decimal("1000"),
            initial_mark_price=Decimal("100"),
            started_ts_ns=1_000,
            identity_namespace="",
        )
    with pytest.raises(ValueError, match="risk_adverse"):
        PaperExchangeSimulator(
            scenario(queue_model=QueueModel.LOG_PROBABILITY),
            initial_equity_usd=Decimal("1000"),
            initial_mark_price=Decimal("100"),
            started_ts_ns=1_000,
        )
    with pytest.raises(ValueError, match="zero synthetic feed latency"):
        PaperExchangeSimulator(
            scenario(feed_latency_offset_ns=1),
            initial_equity_usd=Decimal("1000"),
            initial_mark_price=Decimal("100"),
            started_ts_ns=1_000,
        )

    source = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100"),
        started_ts_ns=1_000,
    )
    restored = source.submit(intent("restored"), accepted_ts_ns=1_000)
    with pytest.raises(ValueError, match="duplicate identities"):
        PaperExchangeSimulator(
            scenario(),
            initial_equity_usd=Decimal("1000"),
            initial_mark_price=Decimal("100"),
            started_ts_ns=1_000,
            restored_orders=(restored, restored),
        )


def test_instruction_rejections_pending_exposure_and_duplicate_intents_are_explicit() -> None:
    simulator = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    pending = simulator.submit(intent("pending"), accepted_ts_ns=1_000)
    assert simulator.pending_exposure() == (Decimal("0.001"), Decimal("0"))
    with pytest.raises(ValueError, match="duplicate paper intent"):
        simulator.submit(pending.intent, accepted_ts_ns=1_001)
    assert simulator.request_cancel("unknown", requested_ts_ns=1_001) is None

    bad_quantity = intent("bad-quantity").model_copy(update={"quantity_base": Decimal("0.0015")})
    bad_price = intent("bad-price").model_copy(update={"limit_price": Decimal("100.5")})
    unsupported_gtc = intent("unsupported-gtc", post_only=False)
    assert (
        simulator.submit(bad_quantity, accepted_ts_ns=1_002).rejection_reason
        == "quantity_not_on_lot_size"
    )
    assert (
        simulator.submit(bad_price, accepted_ts_ns=1_003).rejection_reason
        == "price_not_on_tick_size"
    )
    assert (
        simulator.submit(unsupported_gtc, accepted_ts_ns=1_004).rejection_reason
        == "paper_gtc_limit_must_be_post_only"
    )


def test_ioc_and_non_partial_liquidity_constraints_cancel_without_phantom_fills() -> None:
    simulator = PaperExchangeSimulator(
        scenario(allow_partial_fills=False),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    non_crossing_ioc = intent("non-crossing-ioc", post_only=False).model_copy(
        update={"time_in_force": TimeInForce.IOC}
    )
    simulator.submit(non_crossing_ioc, accepted_ts_ns=1_000)
    ioc_update = simulator.advance(market(1))
    assert ioc_update.orders[0].state is PaperOrderState.CANCELED
    assert ioc_update.fills == ()

    oversized_market = intent(
        "oversized-market",
        kind=OrderKind.MARKET,
        limit_price=None,
        post_only=False,
    ).model_copy(update={"quantity_base": Decimal("2")})
    simulator.submit(oversized_market, accepted_ts_ns=1_200)
    liquidity_update = simulator.advance(market(3))
    assert liquidity_update.orders[0].state is PaperOrderState.CANCELED
    assert liquidity_update.fills == ()


def test_short_close_and_funding_replay_protection_update_account_causally() -> None:
    simulator = PaperExchangeSimulator(
        scenario(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    simulator.submit(
        intent(
            "short",
            side=OrderSide.SELL,
            kind=OrderKind.MARKET,
            limit_price=None,
            post_only=False,
        ),
        accepted_ts_ns=1_000,
    )
    opened = simulator.advance(market(1))
    assert opened.account.position_base == Decimal("-0.001")

    simulator.submit(
        intent(
            "close-short",
            kind=OrderKind.MARKET,
            limit_price=None,
            post_only=False,
        ),
        accepted_ts_ns=1_200,
    )
    closed = simulator.advance(market(3))
    assert closed.account.position_base == 0
    assert closed.account.average_entry_price is None
    assert closed.account.realized_trading_pnl_usd < 0

    settlement = 3_600_000_000_000
    simulator.settle_funding(
        funding_rate=Decimal("0.0001"),
        mark_price=Decimal("100"),
        settlement_ts_ns=settlement,
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        simulator.settle_funding(
            funding_rate=Decimal("0.0001"),
            mark_price=Decimal("100"),
            settlement_ts_ns=settlement,
        )


def test_resting_order_cannot_fill_from_a_trade_observed_before_activation() -> None:
    simulator = PaperExchangeSimulator(
        scenario(entry_latency_ns=100),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    simulator.submit(intent("causal-passive"), accepted_ts_ns=1_000)
    old_trade = KernelTrade(
        exchange_ts_ns=1_050,
        observed_ts_ns=1_050,
        price=Decimal("100"),
        size=Decimal("2"),
        aggressor=AggressorSide.SELLER,
    )
    update = simulator.advance(market(1, trades=(old_trade,)))
    assert update.fills == ()
    assert update.orders[0].state is PaperOrderState.RESTING


def test_market_order_sweeps_visible_levels_with_adverse_prices_and_fees() -> None:
    simulator = PaperExchangeSimulator(
        scenario(taker_slippage_bps="0"),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        started_ts_ns=1_000,
    )
    sweeping = intent(
        "sweep",
        kind=OrderKind.MARKET,
        limit_price=None,
        post_only=False,
    ).model_copy(update={"quantity_base": Decimal("0.002")})
    simulator.submit(sweeping, accepted_ts_ns=1_000)
    two_levels = market(1).model_copy(
        update={
            "asks": (
                KernelBookLevel(price=Decimal("101"), size=Decimal("0.001")),
                KernelBookLevel(price=Decimal("102"), size=Decimal("0.001")),
            )
        }
    )
    update = simulator.advance(two_levels)
    assert [fill.price for fill in update.fills] == [Decimal("101"), Decimal("102")]
    assert sum((fill.quantity_base for fill in update.fills), Decimal("0")) == Decimal("0.002")
    assert update.orders[0].state is PaperOrderState.FILLED
