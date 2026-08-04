"""Pinned NautilusTrader Hyperliquid node construction and lifecycle."""

from __future__ import annotations

import signal
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

from nautilus_trader.adapters.hyperliquid import (
    HYPERLIQUID,
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidLiveDataClientFactory,
    HyperliquidLiveExecClientFactory,
)
from nautilus_trader.adapters.hyperliquid.enums import HyperliquidProductType
from nautilus_trader.common import Environment
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import ExchangeNetwork
from aiquanttrader_native.domain.execution import ExecutionJournalEvent, ExecutionState
from aiquanttrader_native.execution.heartbeat import HeartbeatPublisher
from aiquanttrader_native.execution.journal import ExecutionJournal
from aiquanttrader_native.execution.metrics import ExecutionMetrics
from aiquanttrader_native.execution.secrets import PrivateKey
from aiquanttrader_native.execution.strategy import RiskManagedExecutionStrategy
from aiquanttrader_native.risk.authority import RiskAuthority


@dataclass(frozen=True, slots=True)
class BuiltTradingNode:
    node: TradingNode
    gateway: RiskManagedExecutionStrategy


def build_nautilus_config(bundle: ConfigBundle, private_key: PrivateKey) -> TradingNodeConfig:
    """Map validated native policy to the exact pinned adapter configuration."""

    settings = bundle.settings
    if not settings.execution.enabled or not settings.can_submit_orders:
        raise ValueError("Nautilus execution cannot start while execution is disabled")
    if settings.exchange.account_address is None:
        raise ValueError("Nautilus execution requires an account address")
    environment = (
        HyperliquidEnvironment.TESTNET
        if settings.exchange.network is ExchangeNetwork.TESTNET
        else HyperliquidEnvironment.MAINNET
    )
    provider = InstrumentProviderConfig(
        load_all=True,
        filters={"market_types": ["perp"], "symbols": ["BTC-USD-PERP"]},
    )
    return TradingNodeConfig(
        environment=Environment.LIVE,
        trader_id=TraderId(f"AQT-{settings.environment.upper()}-001"),
        logging=LoggingConfig(
            log_level="INFO",
            log_colors=False,
            print_config=False,
            use_pyo3=True,
        ),
        data_engine=LiveDataEngineConfig(
            validate_data_sequence=True,
            qsize=100_000,
            graceful_shutdown_on_exception=True,
        ),
        risk_engine=LiveRiskEngineConfig(
            bypass=False,
            max_order_submit_rate=f"{settings.risk.max_orders_per_second}/00:00:01",
            max_order_modify_rate=f"{settings.risk.max_cancels_per_second}/00:00:01",
            qsize=10_000,
            graceful_shutdown_on_exception=True,
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=settings.execution.reconcile_on_startup,
            generate_missing_orders=True,
            inflight_check_interval_ms=1_000,
            inflight_check_threshold_ms=settings.execution.unknown_order_timeout_ms,
            inflight_check_retries=5,
            open_check_interval_secs=5.0,
            open_check_threshold_ms=settings.execution.unknown_order_timeout_ms,
            position_check_interval_secs=5.0,
            reconciliation_startup_delay_secs=5.0,
            qsize=10_000,
            graceful_shutdown_on_exception=True,
        ),
        data_clients={
            HYPERLIQUID: HyperliquidDataClientConfig(
                instrument_provider=provider,
                product_types=(HyperliquidProductType.PERP,),
                environment=environment,
                base_url_ws=str(settings.exchange.websocket_url),
                http_timeout_secs=settings.execution.adapter_http_timeout_seconds,
            )
        },
        exec_clients={
            HYPERLIQUID: HyperliquidExecClientConfig(
                private_key=private_key.reveal(),
                account_address=settings.exchange.account_address,
                instrument_provider=provider,
                product_types=(HyperliquidProductType.PERP,),
                environment=environment,
                base_url_ws=str(settings.exchange.websocket_url),
                http_timeout_secs=settings.execution.adapter_http_timeout_seconds,
                ws_post_timeout_secs=settings.execution.adapter_ws_post_timeout_seconds,
                normalize_prices=settings.execution.normalize_prices,
                include_builder_attribution=settings.execution.include_builder_attribution,
            )
        },
        timeout_reconciliation=30.0,
        timeout_disconnection=10.0,
        timeout_shutdown=10.0,
    )


def build_trading_node(
    bundle: ConfigBundle,
    private_key: PrivateKey,
    *,
    journal: ExecutionJournal,
    authority: RiskAuthority,
    heartbeat: HeartbeatPublisher,
    metrics: ExecutionMetrics | None = None,
) -> BuiltTradingNode:
    node = TradingNode(config=build_nautilus_config(bundle, private_key))
    gateway = RiskManagedExecutionStrategy(
        authority=authority,
        journal=journal,
        limits=bundle.settings.risk,
        heartbeat=heartbeat,
        metrics=metrics,
    )
    node.trader.add_strategy(gateway)
    node.add_data_client_factory(HYPERLIQUID, HyperliquidLiveDataClientFactory)
    node.add_exec_client_factory(HYPERLIQUID, HyperliquidLiveExecClientFactory)
    node.build()
    return BuiltTradingNode(node=node, gateway=gateway)


def run_trading_node(
    built: BuiltTradingNode,
    *,
    heartbeat: HeartbeatPublisher,
    heartbeat_interval_ms: int,
    journal: ExecutionJournal,
    unknown_order_timeout_ms: int,
) -> None:
    stop = threading.Event()

    def publish_heartbeat() -> None:
        while not stop.wait(heartbeat_interval_ms / 1_000):
            heartbeat.publish()
            mark_stale_submissions(
                journal,
                cutoff_ts_ns=time.time_ns() - unknown_order_timeout_ms * 1_000_000,
            )

    def request_stop(_signum: int, _frame: object) -> None:
        built.node.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    heartbeat.publish()
    thread = threading.Thread(target=publish_heartbeat, name="execution-heartbeat", daemon=True)
    thread.start()
    try:
        built.node.run(raise_exception=True)
    finally:
        stop.set()
        thread.join(timeout=2)
        heartbeat.set_health(execution_healthy=False, reconciliation_complete=False)
        heartbeat.publish()
        built.node.dispose()


def mark_stale_submissions(journal: ExecutionJournal, *, cutoff_ts_ns: int) -> int:
    def make_event(row: sqlite3.Row) -> ExecutionJournalEvent:
        return ExecutionJournalEvent(
            event_id=str(uuid.uuid4()),
            intent_id=str(row["intent_id"]),
            client_order_id=row["client_order_id"],
            venue_order_id=row["venue_order_id"],
            state=ExecutionState.UNKNOWN,
            event_ts_ns=time.time_ns(),
            filled_quantity_base=row["filled_quantity_base"],
            detail=(
                "unresolved exchange command exceeded its outcome timeout; "
                "Nautilus reconciliation must resolve it"
            ),
            source="reconciliation",
        )

    return journal.mark_stale_submissions_unknown(
        cutoff_ts_ns=cutoff_ts_ns,
        event_factory=make_event,
    )
