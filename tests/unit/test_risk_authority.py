from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aiquanttrader.config.models import ExecutionConfig, RiskLimits
from aiquanttrader.domain.execution import (
    OrderIntent,
    OrderKind,
    RiskReason,
    RiskSnapshot,
    RiskState,
    TimeInForce,
)
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.risk import ApprovalError, KillSwitchStore, RiskAuthority

NOW = 10_000_000_000


def intent(**updates: object) -> OrderIntent:
    values: dict[str, object] = {
        "intent_id": "intent-1",
        "strategy_id": "maker-1",
        "side": OrderSide.BUY,
        "kind": OrderKind.LIMIT,
        "quantity_base": Decimal("0.001"),
        "limit_price": Decimal("100000"),
        "time_in_force": TimeInForce.GTC,
        "post_only": True,
        "reduce_only": False,
        "created_ts_ns": NOW,
        "rationale": "unit test quote",
    }
    values.update(updates)
    return OrderIntent.model_validate(values)


def snapshot(**updates: object) -> RiskSnapshot:
    values: dict[str, object] = {
        "snapshot_ts_ns": NOW,
        "public_data_ts_ns": NOW,
        "private_data_ts_ns": NOW,
        "mark_price": Decimal("100000"),
        "position_base": Decimal("0"),
        "account_equity_usd": Decimal("10000"),
        "day_start_equity_usd": Decimal("10000"),
        "high_water_equity_usd": Decimal("10000"),
        "leverage": Decimal("0"),
        "open_order_count": 0,
        "exchange_connected": True,
        "reconciliation_complete": True,
    }
    values.update(updates)
    return RiskSnapshot.model_validate(values)


def authority(tmp_path: Path, **limit_updates: object) -> RiskAuthority:
    return RiskAuthority(
        RiskLimits.model_validate(limit_updates),
        ExecutionConfig(),
        kill_switch=KillSwitchStore((tmp_path / "operator-kill.json").resolve()),
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW,
        signing_key=b"s" * 32,
    )


def test_approval_is_bound_single_use_and_expiring(tmp_path: Path) -> None:
    risk = authority(tmp_path)
    order = intent()
    state = snapshot()
    decision = risk.evaluate(order, state)

    assert decision.allowed
    assert decision.state is RiskState.ACTIVE
    assert decision.reasons == (RiskReason.APPROVED,)
    risk.consume(decision, order, state)
    with pytest.raises(ApprovalError, match="already been consumed"):
        risk.consume(decision, order, state)
    with pytest.raises(ApprovalError, match="does not match"):
        risk.consume(decision, intent(intent_id="other"), state)

    expired_risk = RiskAuthority(
        RiskLimits(),
        ExecutionConfig(approval_ttl_ms=50),
        kill_switch=KillSwitchStore((tmp_path / "expired-kill.json").resolve()),
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW + 51_000_000,
        signing_key=b"x" * 32,
    )
    issued = RiskAuthority(
        RiskLimits(),
        ExecutionConfig(approval_ttl_ms=50),
        kill_switch=KillSwitchStore((tmp_path / "issued-kill.json").resolve()),
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW,
        signing_key=b"x" * 32,
    ).evaluate(order, state)
    with pytest.raises(ApprovalError, match="expired"):
        expired_risk.consume(issued, order, state)


@pytest.mark.parametrize(
    ("order", "state", "reason"),
    [
        (intent(quantity_base="0.006"), snapshot(), RiskReason.ORDER_SIZE_LIMIT),
        (
            intent(quantity_base="0.003", limit_price="100000"),
            snapshot(),
            RiskReason.ORDER_NOTIONAL_LIMIT,
        ),
        (
            intent(),
            snapshot(position_base="0.02"),
            RiskReason.POSITION_LIMIT,
        ),
        (
            intent(),
            snapshot(pending_buy_base="0.02"),
            RiskReason.INVENTORY_LIMIT,
        ),
        (intent(), snapshot(open_order_count=4), RiskReason.OPEN_ORDER_LIMIT),
        (
            intent(created_ts_ns=NOW - 6_000_000_000),
            snapshot(),
            RiskReason.INTENT_TOO_OLD,
        ),
        (
            intent(created_ts_ns=NOW + 1),
            snapshot(),
            RiskReason.INVALID_TIMESTAMP,
        ),
        (
            intent(reduce_only=True),
            snapshot(position_base="0"),
            RiskReason.NOT_POSITION_REDUCING,
        ),
    ],
)
def test_hard_order_and_exposure_limits(
    tmp_path: Path, order: OrderIntent, state: RiskSnapshot, reason: RiskReason
) -> None:
    decision = authority(tmp_path).evaluate(order, state)
    assert not decision.allowed
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("updates", "expected_state", "reason"),
    [
        ({"operator_kill": True}, RiskState.HALTED, RiskReason.OPERATOR_KILL),
        ({"exchange_connected": False}, RiskState.CANCEL_ONLY, RiskReason.EXCHANGE_DISCONNECTED),
        (
            {"reconciliation_complete": False},
            RiskState.CANCEL_ONLY,
            RiskReason.RECONCILIATION_INCOMPLETE,
        ),
        (
            {"deployment_approved": False},
            RiskState.HALTED,
            RiskReason.DEPLOYMENT_APPROVAL_INVALID,
        ),
        (
            {"account_equity_usd": "1001", "approved_capital_limit_usd": "1000"},
            RiskState.HALTED,
            RiskReason.CAPITAL_LIMIT,
        ),
        (
            {"public_data_ts_ns": NOW - 2_000_000_000},
            RiskState.CANCEL_ONLY,
            RiskReason.PUBLIC_DATA_STALE,
        ),
        (
            {"private_data_ts_ns": NOW - 4_000_000_000},
            RiskState.CANCEL_ONLY,
            RiskReason.PRIVATE_DATA_STALE,
        ),
        (
            {"snapshot_ts_ns": NOW + 1, "public_data_ts_ns": NOW + 1},
            RiskState.CANCEL_ONLY,
            RiskReason.INVALID_TIMESTAMP,
        ),
        (
            {"account_equity_usd": "9900"},
            RiskState.REDUCE_ONLY,
            RiskReason.DAILY_LOSS_LIMIT,
        ),
        (
            {"account_equity_usd": "9800", "day_start_equity_usd": "9800"},
            RiskState.REDUCE_ONLY,
            RiskReason.DRAWDOWN_LIMIT,
        ),
        ({"leverage": "2"}, RiskState.REDUCE_ONLY, RiskReason.LEVERAGE_LIMIT),
    ],
)
def test_health_and_economic_breakers(
    tmp_path: Path,
    updates: dict[str, object],
    expected_state: RiskState,
    reason: RiskReason,
) -> None:
    decision = authority(tmp_path).evaluate(intent(), snapshot(**updates))
    assert not decision.allowed
    assert decision.state is expected_state
    assert reason in decision.reasons


def test_reduce_only_remains_available_after_loss_and_during_flatten(tmp_path: Path) -> None:
    reduce = intent(
        side=OrderSide.SELL,
        quantity_base="0.002",
        reduce_only=True,
        post_only=False,
    )
    loss = snapshot(position_base="0.01", account_equity_usd="9900")
    decision = authority(tmp_path).evaluate(reduce, loss)
    assert decision.allowed
    assert decision.state is RiskState.REDUCE_ONLY

    flatten = snapshot(position_base="0.01", flatten_requested=True)
    decision = authority(tmp_path).evaluate(reduce, flatten)
    assert decision.allowed
    assert decision.state is RiskState.FLATTENING

    disconnected = snapshot(position_base="0.01", flatten_requested=True, exchange_connected=False)
    decision = authority(tmp_path).evaluate(reduce, disconnected)
    assert not decision.allowed
    assert decision.state is RiskState.CANCEL_ONLY


def test_rate_limit_counts_only_consumed_approvals(tmp_path: Path) -> None:
    risk = authority(tmp_path, max_orders_per_second=1)
    first = intent()
    first_decision = risk.evaluate(first, snapshot())
    risk.consume(first_decision, first, snapshot())
    second = risk.evaluate(intent(intent_id="intent-2"), snapshot())
    assert not second.allowed
    assert RiskReason.ORDER_RATE_LIMIT in second.reasons


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": OrderKind.LIMIT, "limit_price": None},
        {"kind": OrderKind.MARKET, "limit_price": "1", "time_in_force": TimeInForce.IOC},
        {
            "kind": OrderKind.MARKET,
            "limit_price": None,
            "post_only": True,
            "time_in_force": TimeInForce.IOC,
        },
        {"post_only": True, "time_in_force": TimeInForce.IOC},
        {"kind": OrderKind.MARKET, "limit_price": None, "post_only": False},
    ],
)
def test_order_instruction_contract_rejects_ambiguous_combinations(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        intent(**updates)


@pytest.mark.parametrize("field", ["public_data_ts_ns", "private_data_ts_ns"])
def test_snapshot_rejects_source_times_after_capture(field: str) -> None:
    with pytest.raises(ValueError, match="cannot follow snapshot"):
        snapshot(**{field: NOW + 1})


def test_persistent_operator_kill_overrides_snapshot(tmp_path: Path) -> None:
    store = KillSwitchStore((tmp_path / "persistent-kill.json").resolve())
    store.activate(actor="operator", reason="test halt")
    risk = RiskAuthority(
        RiskLimits(),
        ExecutionConfig(),
        kill_switch=store,
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW,
        signing_key=b"k" * 32,
    )

    decision = risk.evaluate(intent(), snapshot(operator_kill=False))

    assert not decision.allowed
    assert decision.state is RiskState.HALTED
    assert RiskReason.OPERATOR_KILL in decision.reasons


def test_unresolved_command_limit_is_enforced(tmp_path: Path) -> None:
    risk = RiskAuthority(
        RiskLimits(),
        ExecutionConfig(max_inflight_requests=1),
        kill_switch=KillSwitchStore((tmp_path / "inflight-kill.json").resolve()),
        inflight_count=lambda: 1,
        clock_ns=lambda: NOW,
        signing_key=b"i" * 32,
    )

    decision = risk.evaluate(intent(), snapshot())

    assert not decision.allowed
    assert RiskReason.INFLIGHT_REQUEST_LIMIT in decision.reasons


def test_kill_and_rate_are_rechecked_when_approval_is_consumed(tmp_path: Path) -> None:
    store = KillSwitchStore((tmp_path / "consume-kill.json").resolve())
    risk = RiskAuthority(
        RiskLimits(max_orders_per_second=1),
        ExecutionConfig(),
        kill_switch=store,
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW,
        signing_key=b"c" * 32,
    )
    state = snapshot()
    first = intent(intent_id="first")
    second = intent(intent_id="second")
    first_decision = risk.evaluate(first, state)
    second_decision = risk.evaluate(second, state)
    risk.consume(first_decision, first, state)
    with pytest.raises(ApprovalError, match="rate limit"):
        risk.consume(second_decision, second, state)

    separate = RiskAuthority(
        RiskLimits(),
        ExecutionConfig(),
        kill_switch=store,
        inflight_count=lambda: 0,
        clock_ns=lambda: NOW,
        signing_key=b"d" * 32,
    )
    approved = separate.evaluate(intent(intent_id="kill-race"), state)
    store.activate(actor="operator", reason="race test")
    with pytest.raises(ApprovalError, match="risk state changed"):
        separate.consume(approved, intent(intent_id="kill-race"), state)


def test_every_approved_grid_point_respects_exposure_properties(tmp_path: Path) -> None:
    limits = RiskLimits()
    risk = authority(tmp_path)
    quantities = (Decimal("0.0001"), Decimal("0.001"), Decimal("0.005"), Decimal("0.006"))
    positions = (
        Decimal("-0.02"),
        Decimal("-0.005"),
        Decimal("0"),
        Decimal("0.005"),
        Decimal("0.02"),
    )
    pending = (Decimal("0"), Decimal("0.005"), Decimal("0.02"))

    sequence = 0
    for side in OrderSide:
        for position_base in positions:
            for pending_buy in pending:
                for pending_sell in pending:
                    for quantity in quantities:
                        sequence += 1
                        order = intent(
                            intent_id=f"grid-{sequence}",
                            side=side,
                            quantity_base=quantity,
                        )
                        state = snapshot(
                            position_base=position_base,
                            pending_buy_base=pending_buy,
                            pending_sell_base=pending_sell,
                        )
                        decision = risk.evaluate(order, state)
                        if not decision.allowed:
                            continue
                        signed = quantity if side is OrderSide.BUY else -quantity
                        worst_long = position_base + pending_buy
                        worst_short = position_base - pending_sell
                        if side is OrderSide.BUY:
                            worst_long += quantity
                        else:
                            worst_short -= quantity
                        projected_base = max(abs(worst_long), abs(worst_short))
                        projected_notional = projected_base * state.mark_price

                        assert quantity <= limits.max_order_size_base
                        assert quantity * order.limit_price <= limits.max_order_notional_usd  # type: ignore[operator]
                        assert abs(position_base + signed) <= projected_base
                        assert projected_base <= limits.max_position_size_base
                        assert projected_notional <= limits.max_inventory_notional_usd
                        assert projected_notional / state.account_equity_usd <= limits.max_leverage
