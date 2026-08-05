from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment

from aiquanttrader_native.config import load_config
from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import ExchangeNetwork, ExecutionConfig, RiskLimits
from aiquanttrader_native.domain.execution import (
    ExecutionState,
    OrderIntent,
    OrderKind,
    RiskSnapshot,
    RiskState,
    TimeInForce,
)
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.execution.heartbeat import HeartbeatPublisher
from aiquanttrader_native.execution.journal import ExecutionJournal
from aiquanttrader_native.execution.live import (
    EquityBaselineStore,
    LiveAccountState,
    LiveDecisionPipeline,
)
from aiquanttrader_native.execution.metrics import ExecutionMetrics
from aiquanttrader_native.execution.node import (
    BuiltTradingNode,
    build_nautilus_config,
    build_trading_node,
    mark_stale_submissions,
    run_trading_node,
)
from aiquanttrader_native.execution.secrets import PrivateKey
from aiquanttrader_native.execution.strategy import RiskManagedExecutionStrategy
from aiquanttrader_native.risk import KillSwitchStore, RiskAuthority

ACCOUNT = "0x" + "1" * 40
NOW = 100_000_000_000


def enabled_bundle(config_dir: Path, tmp_path: Path) -> ConfigBundle:
    return load_config(
        config_dir,
        "testnet",
        environ={
            "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-trading-wallet"
            ),
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-control-wallet"
            ),
            "AQT_NATIVE__EXECUTION__ENABLED": "true",
            "AQT_NATIVE__LIVE_STRATEGY__ENABLED": "true",
            "AQT_NATIVE__SENTINEL__ENABLED": "true",
            "AQT_NATIVE__STORAGE__DATA_ROOT": str((tmp_path / "data").resolve()),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str((tmp_path / "state").resolve()),
        },
    )


def test_pinned_nautilus_configuration_is_fail_closed_and_scoped(
    config_dir: Path, tmp_path: Path
) -> None:
    bundle = enabled_bundle(config_dir, tmp_path)
    config = build_nautilus_config(bundle, PrivateKey("1" * 64))
    execution = config.exec_clients[HYPERLIQUID]
    data = config.data_clients[HYPERLIQUID]

    assert execution.environment == HyperliquidEnvironment.TESTNET
    assert execution.account_address == ACCOUNT
    assert execution.normalize_prices is True
    assert execution.include_builder_attribution is False
    assert data.environment == HyperliquidEnvironment.TESTNET
    assert config.risk_engine.bypass is False
    assert config.exec_engine.reconciliation is True

    disabled = load_config(config_dir, "testnet", environ={})
    with pytest.raises(ValueError, match="disabled"):
        build_nautilus_config(disabled, PrivateKey("1" * 64))

    mainnet_mode = ConfigBundle(
        settings=bundle.settings.model_copy(
            update={
                "exchange": bundle.settings.exchange.model_copy(
                    update={"network": ExchangeNetwork.MAINNET}
                )
            }
        ),
        sources=bundle.sources,
        fingerprint=bundle.fingerprint,
    )
    with pytest.raises(ValueError, match="verified deployment admission"):
        build_nautilus_config(mainnet_mode, PrivateKey("1" * 64))


def _intent(**updates: object) -> OrderIntent:
    payload: dict[str, object] = {
        "intent_id": "intent-1",
        "strategy_id": "maker",
        "side": OrderSide.BUY,
        "kind": OrderKind.LIMIT,
        "quantity_base": Decimal("0.001"),
        "limit_price": Decimal("100000"),
        "time_in_force": TimeInForce.GTC,
        "post_only": True,
        "created_ts_ns": NOW,
        "rationale": "adapter unit test",
    }
    payload.update(updates)
    return OrderIntent.model_validate(payload)


def _snapshot(**updates: object) -> RiskSnapshot:
    payload: dict[str, object] = {
        "snapshot_ts_ns": NOW,
        "public_data_ts_ns": NOW,
        "private_data_ts_ns": NOW,
        "mark_price": "100000",
        "position_base": "0",
        "account_equity_usd": "10000",
        "day_start_equity_usd": "10000",
        "high_water_equity_usd": "10000",
        "leverage": "0",
        "open_order_count": 0,
        "exchange_connected": True,
        "reconciliation_complete": True,
    }
    payload.update(updates)
    return RiskSnapshot.model_validate(payload)


def test_gateway_journals_denials_before_any_adapter_call(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    heartbeat = Mock(spec=HeartbeatPublisher)
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "denial-kill.json").resolve()),
            inflight_count=lambda: journal.unresolved_command_count(),
            clock_ns=lambda: NOW,
            signing_key=b"z" * 32,
        ),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=heartbeat,
    )
    decision, client_order_id = strategy.execute_intent(_intent(), _snapshot(operator_kill=True))
    assert not decision.allowed
    assert client_order_id is None
    assert journal.current("intent-1")["state"] == "denied"  # type: ignore[index]
    strategy.on_stop()
    heartbeat.set_health.assert_called_with(execution_healthy=False, reconciliation_complete=False)
    with pytest.raises(RuntimeError, match="not registered"):
        strategy._make_order(_intent())


def test_stale_snapshot_cannot_grant_a_healthy_sentinel_lease(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    heartbeat = Mock(spec=HeartbeatPublisher)
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "stale-kill.json").resolve()),
            inflight_count=lambda: journal.unresolved_command_count(),
            clock_ns=lambda: NOW,
            signing_key=b"h" * 32,
        ),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=heartbeat,
    )

    decision, _ = strategy.execute_intent(
        _intent(),
        _snapshot(public_data_ts_ns=NOW - 2_000_000_000),
    )

    assert not decision.allowed
    heartbeat.set_health.assert_called_once_with(
        execution_healthy=False,
        reconciliation_complete=True,
        valid_for_ms=1500,
    )


def test_gateway_denies_exposure_when_deployment_admission_is_inactive(
    tmp_path: Path,
) -> None:
    class InactiveAdmission:
        capital_limit_usd = Decimal("1000")

        @staticmethod
        def is_active() -> bool:
            return False

        @staticmethod
        def require_active() -> object:
            raise ValueError("inactive")

    journal = ExecutionJournal((tmp_path / "admission.db").resolve())
    heartbeat = Mock(spec=HeartbeatPublisher)
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "admission-kill.json").resolve()),
            inflight_count=journal.unresolved_command_count,
            clock_ns=lambda: NOW,
            signing_key=b"a" * 32,
        ),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=heartbeat,
        admission_guard=InactiveAdmission(),
    )

    decision, client_order_id = strategy.execute_intent(
        _intent(),
        _snapshot(
            account_equity_usd="1000",
            day_start_equity_usd="1000",
            high_water_equity_usd="1000",
        ),
    )

    assert not decision.allowed
    assert client_order_id is None
    assert "deployment_approval_invalid" in journal.events("intent-1")[-1].detail
    journal.close()


def test_gateway_fails_closed_when_admission_revokes_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RevokedAdmission:
        capital_limit_usd = Decimal("10000")

        @staticmethod
        def is_active() -> bool:
            return True

        @staticmethod
        def require_active() -> object:
            raise ValueError("revoked")

    journal = ExecutionJournal((tmp_path / "revoked.db").resolve())
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "revoked-kill.json").resolve()),
            inflight_count=journal.unresolved_command_count,
            clock_ns=lambda: NOW,
            signing_key=b"r" * 32,
        ),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
        admission_guard=RevokedAdmission(),
    )
    monkeypatch.setattr(
        strategy,
        "_make_order",
        lambda intent: SimpleNamespace(client_order_id="cloid-revoked"),
    )

    with pytest.raises(ValueError, match="revoked"):
        strategy.execute_intent(_intent(), _snapshot())

    assert journal.current("intent-1")["state"] == ExecutionState.DENIED.value  # type: ignore[index]
    journal.close()


def test_cancel_budget_and_terminal_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "cancel-budget-kill.json").resolve()),
            inflight_count=lambda: journal.unresolved_command_count(),
        ),
        journal=journal,
        limits=RiskLimits(max_cancels_per_second=1),
        heartbeat=Mock(),
    )
    monkeypatch.setattr("aiquanttrader_native.execution.strategy.time.time_ns", lambda: NOW)
    strategy._consume_cancel_budget()
    with pytest.raises(ValueError, match="cancel rate"):
        strategy._consume_cancel_budget()
    with pytest.raises(ValueError, match="no submitted order"):
        strategy._require_order("missing")
    assert strategy._event_reason(SimpleNamespace(reason="venue reject")) == "venue reject"
    assert strategy._event_reason(SimpleNamespace()) == "SimpleNamespace"


def test_gateway_approved_submit_unknown_and_order_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def make_strategy(path: str) -> tuple[RiskManagedExecutionStrategy, ExecutionJournal]:
        journal = ExecutionJournal((tmp_path / path).resolve())
        strategy = RiskManagedExecutionStrategy(
            authority=RiskAuthority(
                RiskLimits(),
                ExecutionConfig(),
                kill_switch=KillSwitchStore((tmp_path / f"{path}.kill.json").resolve()),
                inflight_count=lambda: journal.unresolved_command_count(),
                clock_ns=lambda: NOW,
                signing_key=b"q" * 32,
            ),
            journal=journal,
            limits=RiskLimits(),
            heartbeat=Mock(),
        )
        return strategy, journal

    strategy, journal = make_strategy("success.db")
    order = SimpleNamespace(client_order_id="cloid-success")
    monkeypatch.setattr(strategy, "_make_order", lambda value: order)
    submit = Mock(
        side_effect=lambda value: (
            (
                journal.current("intent-1")["state"] == "submitted"  # type: ignore[index]
                and journal.current("intent-1")["client_order_id"] == "cloid-success"  # type: ignore[index]
            )
            or pytest.fail("client identity was not durable before adapter dispatch")
        )
    )
    monkeypatch.setattr(strategy, "_submit_nautilus", submit)
    decision, client_order_id = strategy.execute_intent(_intent(), _snapshot())
    assert decision.allowed and client_order_id == "cloid-success"
    submit.assert_called_once_with(order)
    assert journal.current("intent-1")["state"] == "submitted"  # type: ignore[index]

    strategy, journal = make_strategy("unknown.db")
    monkeypatch.setattr(strategy, "_make_order", lambda value: order)
    monkeypatch.setattr(
        strategy,
        "_submit_nautilus",
        Mock(side_effect=RuntimeError("transport timeout")),
    )
    with pytest.raises(RuntimeError, match="timeout"):
        strategy.execute_intent(_intent(), _snapshot())
    assert journal.current("intent-1")["state"] == "unknown"  # type: ignore[index]

    strategy, _ = make_strategy("translation.db")
    factory = Mock()
    factory.limit.return_value = "limit-order"
    factory.market.return_value = "market-order"
    monkeypatch.setattr(strategy, "_get_order_factory", lambda: factory)
    assert strategy._make_order(_intent()) == "limit-order"
    market = _intent(
        kind=OrderKind.MARKET,
        limit_price=None,
        post_only=False,
        time_in_force=TimeInForce.IOC,
    )
    assert strategy._make_order(market) == "market-order"


def _seed_open_order(
    journal: ExecutionJournal,
    intent_id: str = "intent-1",
    *,
    accepted: bool = True,
) -> None:
    from aiquanttrader_native.domain.execution import ExecutionJournalEvent, ExecutionState

    journal.begin(
        ExecutionJournalEvent(
            event_id=f"{intent_id}-pending",
            intent_id=intent_id,
            state=ExecutionState.PENDING_SUBMIT,
            event_ts_ns=1,
            detail="pending",
            source="risk",
        )
    )
    journal.append(
        ExecutionJournalEvent(
            event_id=f"{intent_id}-submitted",
            intent_id=intent_id,
            client_order_id=f"cloid-{intent_id}",
            state=ExecutionState.SUBMITTED,
            event_ts_ns=2,
            detail="submitted",
            source="nautilus",
        )
    )
    if not accepted:
        return
    journal.append(
        ExecutionJournalEvent(
            event_id=f"{intent_id}-accepted",
            intent_id=intent_id,
            client_order_id=f"cloid-{intent_id}",
            state=ExecutionState.ACCEPTED,
            event_ts_ns=3,
            detail="accepted",
            source="nautilus",
        )
    )


def test_gateway_replace_cancel_and_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = ExecutionJournal((tmp_path / "orders.db").resolve())
    _seed_open_order(journal)
    strategy = RiskManagedExecutionStrategy(
        authority=RiskAuthority(
            RiskLimits(),
            ExecutionConfig(),
            kill_switch=KillSwitchStore((tmp_path / "order-kill.json").resolve()),
            inflight_count=lambda: journal.unresolved_command_count(),
            clock_ns=lambda: NOW,
            signing_key=b"m" * 32,
        ),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
    )
    cached = SimpleNamespace(is_closed=False, filled_qty=Decimal("0"))
    monkeypatch.setattr(strategy, "_cached_order", lambda value: cached)
    modify = Mock()
    monkeypatch.setattr(strategy, "_modify_nautilus", modify)
    replacement = _intent(intent_id="replacement", limit_price="99900")
    assert strategy.replace_order(
        intent_id="intent-1", replacement=replacement, snapshot=_snapshot()
    ).allowed
    modify.assert_called_once()
    assert journal.current("intent-1")["state"] == "pending_modify"  # type: ignore[index]

    event = SimpleNamespace(
        client_order_id="cloid-intent-1",
        venue_order_id="42",
        reason="test rejection",
        last_qty=Decimal("0.0005"),
    )
    strategy.on_order_updated(event)
    assert journal.current("intent-1")["state"] == "accepted"  # type: ignore[index]
    strategy.on_order_filled(event)
    assert journal.current("intent-1")["state"] == "partially_filled"  # type: ignore[index]

    cancel = Mock()
    monkeypatch.setattr(strategy, "_cancel_nautilus", cancel)
    strategy.cancel("intent-1")
    cancel.assert_called_once_with(cached)
    strategy.on_order_cancel_rejected(event)
    assert journal.current("intent-1")["state"] == "accepted"  # type: ignore[index]
    strategy.cancel("intent-1")
    strategy.on_order_canceled(event)
    assert journal.current("intent-1")["state"] == "canceled"  # type: ignore[index]
    with pytest.raises(ValueError, match="terminal"):
        strategy.cancel("intent-1")

    cancel_all = Mock()
    monkeypatch.setattr(strategy, "_cancel_all_nautilus", cancel_all)
    strategy.cancel_all()
    cancel_all.assert_called_once()


def test_cancel_exception_becomes_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = ExecutionJournal((tmp_path / "cancel-unknown.db").resolve())
    _seed_open_order(journal)
    strategy = RiskManagedExecutionStrategy(
        authority=Mock(),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
    )
    monkeypatch.setattr(strategy, "_cached_order", lambda value: SimpleNamespace())
    monkeypatch.setattr(
        strategy,
        "_cancel_nautilus",
        Mock(side_effect=RuntimeError("transport timeout")),
    )

    with pytest.raises(RuntimeError, match="timeout"):
        strategy.cancel("intent-1")

    assert journal.current("intent-1")["state"] == "unknown"  # type: ignore[index]


def test_gateway_replace_and_cancel_adapter_failures_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gateway(name: str) -> tuple[RiskManagedExecutionStrategy, ExecutionJournal]:
        journal = ExecutionJournal((tmp_path / f"{name}.db").resolve())
        _seed_open_order(journal)
        return (
            RiskManagedExecutionStrategy(
                authority=RiskAuthority(
                    RiskLimits(),
                    ExecutionConfig(),
                    kill_switch=KillSwitchStore((tmp_path / f"{name}-kill.json").resolve()),
                    inflight_count=journal.unresolved_command_count,
                    clock_ns=lambda: NOW,
                    signing_key=b"f" * 32,
                ),
                journal=journal,
                limits=RiskLimits(),
                heartbeat=Mock(),
            ),
            journal,
        )

    strategy, journal = gateway("missing-cache")
    monkeypatch.setattr(strategy, "_cached_order", lambda value: None)
    with pytest.raises(ValueError, match="absent"):
        strategy.replace_order(
            intent_id="intent-1",
            replacement=_intent(intent_id="replacement"),
            snapshot=_snapshot(),
        )
    with pytest.raises(ValueError, match="absent"):
        strategy.cancel("intent-1")
    journal.close()

    strategy, journal = gateway("invalid-replacement")
    monkeypatch.setattr(strategy, "_cached_order", lambda value: SimpleNamespace())
    with pytest.raises(ValueError, match="priced limit"):
        strategy.replace_order(
            intent_id="intent-1",
            replacement=_intent(
                intent_id="replacement",
                kind=OrderKind.MARKET,
                limit_price=None,
                post_only=False,
                time_in_force=TimeInForce.IOC,
            ),
            snapshot=_snapshot(),
        )
    denied = strategy.replace_order(
        intent_id="intent-1",
        replacement=_intent(intent_id="replacement-denied"),
        snapshot=_snapshot(operator_kill=True),
    )
    assert not denied.allowed
    journal.close()

    strategy, journal = gateway("modify-unknown")
    monkeypatch.setattr(strategy, "_cached_order", lambda value: SimpleNamespace())
    monkeypatch.setattr(
        strategy,
        "_modify_nautilus",
        Mock(side_effect=RuntimeError("transport timeout")),
    )
    with pytest.raises(RuntimeError, match="timeout"):
        strategy.replace_order(
            intent_id="intent-1",
            replacement=_intent(intent_id="replacement"),
            snapshot=_snapshot(),
        )
    assert journal.current("intent-1")["state"] == ExecutionState.UNKNOWN.value  # type: ignore[index]

    monkeypatch.setattr(
        strategy,
        "_cancel_all_nautilus",
        Mock(side_effect=RuntimeError("cancel-all timeout")),
    )
    with pytest.raises(RuntimeError, match="cancel-all"):
        strategy.cancel_all()
    journal.close()


def test_gateway_event_callbacks_and_start_health(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = Mock()
    journal.by_client_order_id.return_value = {"intent_id": "intent-1"}
    journal.unresolved_command_count.return_value = 0
    heartbeat = Mock()
    strategy = RiskManagedExecutionStrategy(
        authority=Mock(),
        journal=journal,
        limits=RiskLimits(),
        heartbeat=heartbeat,
    )
    subscribe = Mock()
    monkeypatch.setattr(strategy, "_subscribe_quotes", subscribe)
    strategy.on_start()
    subscribe.assert_called_once()
    heartbeat.set_health.assert_called_with(execution_healthy=False, reconciliation_complete=False)

    event = SimpleNamespace(
        client_order_id="cloid",
        venue_order_id="7",
        reason="venue reason",
        last_qty=Decimal("0.001"),
    )
    for callback in (
        strategy.on_order_submitted,
        strategy.on_order_accepted,
        strategy.on_order_rejected,
        strategy.on_order_denied,
        strategy.on_order_canceled,
        strategy.on_order_expired,
        strategy.on_order_modify_rejected,
    ):
        callback(event)
    assert journal.append.call_count == 7

    journal.by_client_order_id.return_value = None
    strategy.on_order_accepted(event)
    assert journal.append.call_count == 7

    journal.by_client_order_id.return_value = {"intent_id": "intent-1"}
    monkeypatch.setattr(
        strategy,
        "_cached_order",
        lambda value: SimpleNamespace(is_closed=True, filled_qty=Decimal("0.001")),
    )
    strategy.on_order_filled(event)
    assert journal.append.call_args.args[0].state.value == "filled"


def test_gateway_persists_denial_and_expiry_as_terminal_events(tmp_path: Path) -> None:
    denied_journal = ExecutionJournal((tmp_path / "denied-event.db").resolve())
    _seed_open_order(denied_journal, accepted=False)
    denied = RiskManagedExecutionStrategy(
        authority=Mock(),
        journal=denied_journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
    )
    event = SimpleNamespace(client_order_id="cloid-intent-1", venue_order_id=None)
    denied.on_order_denied(event)
    assert denied_journal.current("intent-1")["state"] == ExecutionState.DENIED.value  # type: ignore[index]
    denied_journal.close()

    expired_journal = ExecutionJournal((tmp_path / "expired-event.db").resolve())
    _seed_open_order(expired_journal)
    expired = RiskManagedExecutionStrategy(
        authority=Mock(),
        journal=expired_journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
    )
    expired.on_order_expired(event)
    assert expired_journal.current("intent-1")["state"] == ExecutionState.CANCELED.value  # type: ignore[index]
    expired_journal.close()


def _live_gateway(
    tmp_path: Path,
) -> tuple[
    RiskManagedExecutionStrategy,
    Mock,
    Mock,
    Mock,
]:
    pipeline = Mock()
    journal = Mock()
    journal.unresolved_command_count.return_value = 0
    journal.unknown_command_count.return_value = 0
    authority = Mock()
    metrics = Mock(spec=ExecutionMetrics)
    strategy = RiskManagedExecutionStrategy(
        authority=authority,
        journal=journal,
        limits=RiskLimits(),
        heartbeat=Mock(),
        metrics=metrics,
        live_pipeline=pipeline,
        equity_baselines=EquityBaselineStore(
            (tmp_path / "live-equity.json").resolve(),
            account_address=ACCOUNT,
        ),
        connectivity_probe=lambda: True,
    )
    return strategy, pipeline, journal, metrics


def _cycle(
    *,
    submit: tuple[OrderIntent, ...] = (),
    cancel: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        transition=SimpleNamespace(
            decision=SimpleNamespace(submit=submit, cancel_intent_ids=cancel),
        ),
        features=SimpleNamespace(ready=True),
    )


def test_live_gateway_requires_complete_dependencies(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        RiskManagedExecutionStrategy(
            authority=Mock(),
            journal=Mock(),
            limits=RiskLimits(),
            heartbeat=Mock(),
            live_pipeline=Mock(),
        )


def test_live_gateway_risk_overrides_alpha_and_drains_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, pipeline, _journal, metrics = _live_gateway(tmp_path)
    market = SimpleNamespace()
    cancel_all = Mock()
    monkeypatch.setattr(strategy, "cancel_all", cancel_all)
    monkeypatch.setattr(
        strategy, "_live_risk_snapshot", lambda value: _snapshot(open_order_count=1)
    )
    authority = cast(Mock, strategy._authority)
    authority.state.return_value = (RiskState.CANCEL_ONLY, ())

    strategy._process_live_market(market)

    cancel_all.assert_called_once()
    pipeline.decide.assert_not_called()
    metrics.observe_live_cycle.assert_called_with(result="risk_blocked")

    cancel_all.reset_mock()
    authority.state.return_value = (RiskState.ACTIVE, ())
    strategy._process_live_market(market)
    cancel_all.assert_not_called()
    metrics.observe_live_cycle.assert_called_with(result="risk_recovery_drain")

    strategy._risk_cancel_pending = False
    strategy._startup_order_drain = True
    strategy._process_live_market(market)
    cancel_all.assert_called_once()
    metrics.observe_live_cycle.assert_called_with(result="startup_drain")


def test_live_gateway_dispatches_submits_and_cancel_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, pipeline, journal, metrics = _live_gateway(tmp_path)
    market = SimpleNamespace()
    snapshot = _snapshot()
    monkeypatch.setattr(strategy, "_live_risk_snapshot", lambda value: snapshot)
    cast(Mock, strategy._authority).state.return_value = (RiskState.ACTIVE, ())
    intent = _intent(intent_id="live-intent")
    pipeline.decide.return_value = _cycle(submit=(intent,))
    allowed = SimpleNamespace(allowed=True)
    execute = Mock(return_value=(allowed, "cloid-live"))
    monkeypatch.setattr(strategy, "execute_intent", execute)

    strategy._process_live_market(market)

    execute.assert_called_once_with(intent, snapshot)
    pipeline.commit.assert_called_with(
        pipeline.decide.return_value,
        dispatched_intent_ids={"live-intent"},
        dispatched_cancel_ids=set(),
    )
    metrics.observe_live_action.assert_called_with("submit", "dispatched")

    pipeline.reset_mock()
    pipeline.decide.return_value = _cycle(cancel=("live-intent",))
    journal.current.return_value = {"state": ExecutionState.ACCEPTED.value}
    cancel = Mock()
    monkeypatch.setattr(strategy, "cancel", cancel)
    strategy._process_live_market(market)
    cancel.assert_called_once_with("live-intent")
    assert "live-intent" in strategy._pending_strategy_cancels
    metrics.observe_live_action.assert_called_with("cancel", "dispatched")

    cancel.reset_mock()
    strategy._process_live_market(market)
    cancel.assert_not_called()

    strategy._pending_strategy_cancels.clear()
    journal.current.return_value = {"state": ExecutionState.CANCELED.value}
    strategy._process_live_market(market)
    pipeline.release_intent.assert_called_with("live-intent")

    journal.current.return_value = None
    with pytest.raises(ValueError, match="unjournaled intent"):
        strategy._process_live_market(market)


def test_live_gateway_builds_fresh_cache_bound_risk_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, _pipeline, journal, metrics = _live_gateway(tmp_path)
    now = 1_800_000_000_000_000_000
    market = SimpleNamespace(
        bids=(SimpleNamespace(price=Decimal("99900")),),
        asks=(SimpleNamespace(price=Decimal("100100")),),
        observed_ts_ns=now - 100,
    )
    account = LiveAccountState(
        equity_usd=Decimal("10000"),
        position_base=Decimal("0.001"),
        pending_buy_base=Decimal("0.002"),
        pending_sell_base=Decimal("0.003"),
        open_order_count=2,
    )
    monkeypatch.setattr(strategy, "_live_account_state", lambda: account)
    monkeypatch.setattr("aiquanttrader_native.execution.strategy.time.time_ns", lambda: now)
    strategy._reconciliation_complete = True
    strategy._mark_price = (Decimal("100050"), now - 50)

    snapshot = strategy._live_risk_snapshot(market)

    assert snapshot.mark_price == Decimal("100050")
    assert snapshot.reconciliation_complete is True
    assert snapshot.pending_sell_base == Decimal("0.003")
    journal.unknown_command_count.assert_called_once()
    metrics.set_live_account.assert_called_once_with(equity_usd=10000.0, position_base=0.001)

    strategy._mark_price = (Decimal("200000"), now - 2_000_000_000)
    journal.unknown_command_count.return_value = 1
    stale_mark = strategy._live_risk_snapshot(market)
    assert stale_mark.mark_price == Decimal("100000")
    assert stale_mark.reconciliation_complete is False


def test_live_gateway_start_subscriptions_and_public_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, pipeline, journal, metrics = _live_gateway(tmp_path)
    pipeline.artifacts.feature_config.depth_levels = 7
    subscriptions = {
        name: Mock()
        for name in (
            "subscribe_order_book_deltas",
            "subscribe_trade_ticks",
            "subscribe_mark_prices",
            "subscribe_funding_rates",
        )
    }
    for name, callback in subscriptions.items():
        monkeypatch.setattr(strategy, name, callback)
    monkeypatch.setattr(strategy, "_open_orders", lambda: [])

    strategy.on_start()

    subscriptions["subscribe_order_book_deltas"].assert_called_once()
    assert subscriptions["subscribe_order_book_deltas"].call_args.kwargs == {
        "depth": 7,
        "managed": True,
    }
    subscriptions["subscribe_trade_ticks"].assert_called_once()
    assert strategy._reconciliation_complete is True
    strategy.on_trade_tick("trade")
    pipeline.market.observe_trade.assert_called_once_with("trade")

    strategy.on_mark_price(
        SimpleNamespace(
            instrument_id="BTC-USD-PERP.HYPERLIQUID",
            value="100123",
            ts_init=123,
        )
    )
    strategy.on_funding_rate(
        SimpleNamespace(
            instrument_id="BTC-USD-PERP.HYPERLIQUID",
            rate="0.0001",
        )
    )
    assert strategy._mark_price == (Decimal("100123"), 123)
    assert strategy._funding_rate == Decimal("0.0001")

    strategy.on_mark_price(SimpleNamespace(instrument_id="ETH-USD-PERP.HYPERLIQUID"))
    strategy.on_funding_rate(SimpleNamespace(instrument_id="ETH-USD-PERP.HYPERLIQUID"))
    assert strategy._mark_price == (Decimal("100123"), 123)
    assert strategy._funding_rate == Decimal("0.0001")
    journal.unresolved_command_count.assert_called()
    metrics.set_operational_state.assert_called()


def test_live_gateway_start_cancel_drains_and_book_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy, pipeline, _journal, metrics = _live_gateway(tmp_path)
    monkeypatch.setattr(strategy, "_subscribe_live_data", Mock())
    monkeypatch.setattr(strategy, "_open_orders", lambda: [object()])
    cancel_all = Mock()
    monkeypatch.setattr(strategy, "cancel_all", cancel_all)

    strategy.on_start()

    cancel_all.assert_called_once()
    assert strategy._startup_order_drain is True
    assert strategy._risk_cancel_pending is True

    fake_cache = SimpleNamespace(order_book=lambda instrument_id: "book")
    monkeypatch.setattr(RiskManagedExecutionStrategy, "cache", fake_cache, raising=False)
    pipeline.market.observe_book.return_value = "market"
    process = Mock()
    monkeypatch.setattr(strategy, "_process_live_market", process)
    deltas = SimpleNamespace(instrument_id="BTC-USD-PERP.HYPERLIQUID")
    strategy.on_order_book_deltas(deltas)
    process.assert_called_once_with("market")

    fake_cache.order_book = lambda instrument_id: None
    cancel_all.reset_mock()
    with pytest.raises(ValueError, match="unavailable"):
        strategy.on_order_book_deltas(deltas)
    cancel_all.assert_called_once()
    metrics.observe_live_cycle.assert_called_with(result="error")

    pipeline.market.observe_trade.side_effect = ValueError("trade overflow")
    cancel_all.reset_mock()
    with pytest.raises(ValueError, match="overflow"):
        strategy.on_trade_tick("trade")
    cancel_all.assert_called_once()


def test_execution_metrics_expose_live_pipeline_state() -> None:
    metrics = ExecutionMetrics()
    metrics.observe_live_cycle(result="processed", feature_ready=True)
    metrics.observe_live_cycle(result="risk_blocked")
    metrics.observe_live_action("submit", "dispatched")
    metrics.set_live_account(equity_usd=10000.0, position_base=-0.001)

    samples = {
        sample.name: sample.value
        for metric in metrics.registry.collect()
        for sample in metric.samples
    }
    assert samples["aqt_execution_feature_ready"] == 1
    assert samples["aqt_execution_account_equity_usd"] == 10000
    assert samples["aqt_execution_position_base"] == -0.001


def test_node_factory_wires_only_hyperliquid_execution_owner(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = enabled_bundle(config_dir, tmp_path)
    calls: list[tuple[str, object]] = []

    class FakeTrader:
        def add_strategy(self, strategy: object) -> None:
            calls.append(("strategy", strategy))

    class FakeNode:
        def __init__(self, config: object) -> None:
            calls.append(("config", config))
            self.trader = FakeTrader()

        def add_data_client_factory(self, venue: object, factory: object) -> None:
            calls.append(("data", venue))

        def add_exec_client_factory(self, venue: object, factory: object) -> None:
            calls.append(("exec", venue))

        def build(self) -> None:
            calls.append(("build", True))

    fake_gateway = object()
    gateway_arguments: dict[str, object] = {}

    def make_gateway(**kwargs: object) -> object:
        gateway_arguments.update(kwargs)
        return fake_gateway

    monkeypatch.setattr("aiquanttrader_native.execution.node.TradingNode", FakeNode)
    monkeypatch.setattr(
        "aiquanttrader_native.execution.node.RiskManagedExecutionStrategy",
        make_gateway,
    )
    result = build_trading_node(
        bundle,
        PrivateKey("1" * 64),
        config_dir=config_dir,
        journal=Mock(),
        authority=Mock(),
        heartbeat=Mock(),
    )
    assert result.gateway is fake_gateway
    assert ("data", HYPERLIQUID) in calls
    assert ("exec", HYPERLIQUID) in calls
    assert ("build", True) in calls
    pipeline = gateway_arguments["live_pipeline"]
    assert isinstance(pipeline, LiveDecisionPipeline)
    assert pipeline.artifacts.strategy_config.strategy_id == "order-flow-scalper-v1"
    assert gateway_arguments["connectivity_probe"] is not None


def test_node_lifecycle_and_startup_unknown_marking(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    from aiquanttrader_native.domain.execution import ExecutionJournalEvent, ExecutionState

    journal.begin(
        ExecutionJournalEvent(
            event_id="event-1",
            intent_id="intent-1",
            state=ExecutionState.PENDING_SUBMIT,
            event_ts_ns=1,
            detail="test",
            source="risk",
        )
    )
    assert mark_stale_submissions(journal, cutoff_ts_ns=2) == 1
    assert journal.current("intent-1")["state"] == "unknown"  # type: ignore[index]

    class FakeNode:
        def __init__(self) -> None:
            self.ran = False
            self.disposed = False

        def run(self, raise_exception: bool) -> None:
            self.ran = raise_exception

        def stop(self) -> None:
            pass

        def dispose(self) -> None:
            self.disposed = True

    node = FakeNode()
    heartbeat = Mock()
    run_trading_node(
        BuiltTradingNode(node=node, gateway=Mock()),  # type: ignore[arg-type]
        heartbeat=heartbeat,
        heartbeat_interval_ms=250,
        journal=journal,
        unknown_order_timeout_ms=5_000,
    )
    assert node.ran and node.disposed
    assert heartbeat.publish.call_count >= 2
