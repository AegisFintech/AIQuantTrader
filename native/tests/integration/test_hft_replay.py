from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import numpy as np
import pytest
from hftbacktest import BUY_EVENT, DEPTH_EVENT, SELL_EVENT, TRADE_EVENT, event_dtype
from hftbacktest.data.validation import correct_event_order

from aiquanttrader_native.backtest.models import (
    CalibrationState,
    ExecutionScenario,
    FundingObservation,
    PositionObservation,
    QueueModel,
)
from aiquanttrader_native.backtest.replay import HftReplaySession, calculate_funding_cashflows
from aiquanttrader_native.domain.market import OrderSide


def synthetic_events() -> np.ndarray[Any, np.dtype[Any]]:
    rows = [
        (DEPTH_EVENT | BUY_EVENT, 1_000, 1_100, 100.0, 5.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 1_000, 1_100, 101.0, 5.0, 0, 0, 0.0),
        (TRADE_EVENT | SELL_EVENT, 2_000, 2_100, 100.0, 3.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 2_000, 2_100, 100.0, 2.0, 0, 0, 0.0),
        (TRADE_EVENT | SELL_EVENT, 3_000, 3_100, 100.0, 4.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 3_000, 3_100, 100.0, 1.0, 0, 0, 0.0),
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


def scenario(*, trade_flow: str = "1") -> ExecutionScenario:
    return ExecutionScenario(
        scenario_id=f"synthetic-{trade_flow}",
        calibration_state=CalibrationState.CALIBRATED,
        calibration_sha256="c" * 64,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        entry_latency_ns=10,
        response_latency_ns=10,
        feed_latency_offset_ns=0,
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("5"),
        queue_model=QueueModel.RISK_ADVERSE,
        allow_partial_fills=True,
        book_liquidity_multiplier=Decimal("1"),
        trade_flow_multiplier=Decimal(trade_flow),
        taker_slippage_bps=Decimal("2"),
        funding_rate_multiplier=Decimal("1"),
    )


def run_replay(config: ExecutionScenario) -> bytes:
    with HftReplaySession(
        events=synthetic_events(), dataset_sha256="d" * 64, scenario=config
    ) as session:
        session.start()
        session.submit_limit(
            order_id=1,
            side=OrderSide.BUY,
            price=Decimal("100"),
            quantity_base=Decimal("2"),
        )
        session.advance_until_end()
        result = session.result(
            funding=(
                FundingObservation(
                    settlement_ts_ns=3_100,
                    funding_rate=Decimal("0.001"),
                    oracle_price=Decimal("100"),
                ),
            )
        )
    return result.canonical_bytes()


def test_synthetic_queue_latency_fee_partial_fill_and_funding_are_deterministic() -> None:
    first = run_replay(scenario())
    second = run_replay(scenario())
    assert first == second

    from aiquanttrader_native.backtest.models import ReplayResult

    result = ReplayResult.model_validate_json(first)
    assert result.ending_position_base == Decimal("2")
    assert sum(fill.quantity_base for fill in result.fills) == Decimal("2")
    assert all(fill.maker for fill in result.fills)
    assert result.exchange_fee_usd == Decimal("0.02")
    assert result.funding_cashflow_usd == Decimal("-0.2")
    assert result.explicit_slippage_usd == Decimal("0")
    assert result.marked_equity_usd == Decimal("0.78")


def test_pessimistic_trade_flow_reduces_passive_fills() -> None:
    from aiquanttrader_native.backtest.models import ReplayResult

    baseline = ReplayResult.model_validate_json(run_replay(scenario()))
    pessimistic = ReplayResult.model_validate_json(run_replay(scenario(trade_flow="0.25")))
    assert baseline.ending_position_base == Decimal("2")
    assert pessimistic.ending_position_base == Decimal("0")
    assert pessimistic.fills == ()


def test_funding_uses_position_known_at_each_hour_and_rejects_unsorted_input() -> None:
    positions = (
        PositionObservation(ts_ns=10, position_base=Decimal("1")),
        PositionObservation(ts_ns=30, position_base=Decimal("-2")),
    )
    funding = (
        FundingObservation(
            settlement_ts_ns=20,
            funding_rate=Decimal("0.001"),
            oracle_price=Decimal("100"),
        ),
        FundingObservation(
            settlement_ts_ns=40,
            funding_rate=Decimal("-0.002"),
            oracle_price=Decimal("110"),
        ),
    )
    flows = calculate_funding_cashflows(positions=positions, funding=funding)
    assert [flow.cashflow_usd for flow in flows] == [Decimal("-0.100"), Decimal("-0.440")]

    with pytest.raises(ValueError, match="strictly increasing"):
        calculate_funding_cashflows(positions=tuple(reversed(positions)), funding=funding)
    with pytest.raises(ValueError, match="strictly increasing"):
        calculate_funding_cashflows(positions=positions, funding=tuple(reversed(funding)))
    with pytest.raises(ValueError, match="negative"):
        calculate_funding_cashflows(
            positions=positions,
            funding=funding,
            multiplier=Decimal("-1"),
        )


def test_replay_session_guards_lifecycle_and_order_identity() -> None:
    session = HftReplaySession(
        events=synthetic_events(), dataset_sha256="d" * 64, scenario=scenario()
    )
    with pytest.raises(RuntimeError, match="has not started"):
        session.result()
    session.start()
    with pytest.raises(RuntimeError, match="already started"):
        session.start()
    with pytest.raises(ValueError, match="positive and unique"):
        session.submit_limit(
            order_id=0,
            side=OrderSide.BUY,
            price=Decimal("100"),
            quantity_base=Decimal("1"),
        )
    with pytest.raises(ValueError, match="unknown"):
        session.cancel(99)
    with pytest.raises(ValueError, match="timeout"):
        session.advance(0)
    for settlement_ts_ns in (999, 3_101):
        with pytest.raises(ValueError, match="outside the replay interval"):
            session.result(
                funding=(
                    FundingObservation(
                        settlement_ts_ns=settlement_ts_ns,
                        funding_rate=Decimal("0.001"),
                        oracle_price=Decimal("100"),
                    ),
                )
            )
    session.close()
    session.close()
    with pytest.raises(RuntimeError, match="not active"):
        session.advance()
