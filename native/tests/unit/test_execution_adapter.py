from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nautilus_trader.adapters.hyperliquid import HYPERLIQUID
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment

from aiquanttrader_native.config import load_config
from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import ExchangeNetwork, ExecutionConfig, RiskLimits
from aiquanttrader_native.domain.execution import (
    OrderIntent,
    OrderKind,
    RiskSnapshot,
    TimeInForce,
)
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.execution.heartbeat import HeartbeatPublisher
from aiquanttrader_native.execution.journal import ExecutionJournal
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


def _seed_open_order(journal: ExecutionJournal, intent_id: str = "intent-1") -> None:
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
        strategy.on_order_modify_rejected,
    ):
        callback(event)
    assert journal.append.call_count == 6

    journal.by_client_order_id.return_value = None
    strategy.on_order_accepted(event)
    assert journal.append.call_count == 6

    journal.by_client_order_id.return_value = {"intent_id": "intent-1"}
    monkeypatch.setattr(
        strategy,
        "_cached_order",
        lambda value: SimpleNamespace(is_closed=True, filled_qty=Decimal("0.001")),
    )
    strategy.on_order_filled(event)
    assert journal.append.call_args.args[0].state.value == "filled"


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
    monkeypatch.setattr("aiquanttrader_native.execution.node.TradingNode", FakeNode)
    monkeypatch.setattr(
        "aiquanttrader_native.execution.node.RiskManagedExecutionStrategy",
        lambda **kwargs: fake_gateway,
    )
    result = build_trading_node(
        bundle,
        PrivateKey("1" * 64),
        journal=Mock(),
        authority=Mock(),
        heartbeat=Mock(),
    )
    assert result.gateway is fake_gateway
    assert ("data", HYPERLIQUID) in calls
    assert ("exec", HYPERLIQUID) in calls
    assert ("build", True) in calls


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
