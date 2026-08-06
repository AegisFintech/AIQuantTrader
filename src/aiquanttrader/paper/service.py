"""Live raw-first public feed orchestration for credential-free paper trading."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Literal

from prometheus_client import CollectorRegistry

from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.config.models import ExchangeNetwork
from aiquanttrader.domain.market import FundingEvent, MarkPriceEvent
from aiquanttrader.market_data.catalog import ManifestCatalog
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.market_data.metrics import RecorderMetrics
from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.market_data.recorder import (
    MarketDataRecorder,
    SocketFactory,
    default_socket_factory,
)
from aiquanttrader.paper.config import PaperArtifacts
from aiquanttrader.paper.engine import PaperTradingEngine
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.market import LiveMarketStateAssembler
from aiquanttrader.paper.metrics import PaperMetrics
from aiquanttrader.paper.models import PaperRunManifest, PaperRuntimeStatus
from aiquanttrader.risk.kill_switch import KillSwitchStore


class PaperLiveService:
    def __init__(
        self,
        *,
        bundle: ConfigBundle,
        artifacts: PaperArtifacts,
        journal: PaperJournal,
        kill_switch: KillSwitchStore,
        code_identity: str,
        registry: CollectorRegistry,
        socket_factory: SocketFactory | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        settings = bundle.settings
        if settings.mode.value != "paper" or not settings.paper.enabled:
            raise ValueError("paper service requires the enabled paper environment")
        if settings.execution.enabled or any(
            value is not None
            for value in (
                settings.exchange.account_address,
                settings.exchange.trading_wallet_secret_path,
                settings.exchange.control_wallet_secret_path,
            )
        ):
            raise ValueError("paper service refuses exchange execution capability")
        self.bundle = bundle
        self.artifacts = artifacts
        self.journal = journal
        self.kill_switch = kill_switch
        self.code_identity = code_identity
        self.clock_ns = clock_ns
        self.status_path = settings.storage.state_root / "paper" / "status.json"
        self._assembler = LiveMarketStateAssembler(
            depth_levels=artifacts.feature_config.depth_levels,
            maximum_input_age_ns=artifacts.feature_config.maximum_input_age_ns,
        )
        self._observed_stale_trade_exclusions = 0
        self._paper_metrics = PaperMetrics(registry)
        self._recorder_metrics = RecorderMetrics.create(registry)
        self._engine: PaperTradingEngine | None = None
        self._mark_price: Decimal | None = None
        self._funding_rate = Decimal("0")
        self._next_funding_ts_ns: int | None = None
        self._last_context_receive_ns: int | None = None
        self._last_context_wall_ns: int | None = None
        self._last_frame_wall_ns: int | None = None
        self._last_error_code: str | None = None
        self._recorder_connected = False
        self._socket_factory = socket_factory

    @property
    def engine(self) -> PaperTradingEngine | None:
        return self._engine

    async def consume_frame(self, frame: ParsedFrame) -> None:
        self._last_frame_wall_ns = self.clock_ns()
        self._recorder_connected = True
        if frame.is_control:
            return
        for event in frame.events:
            if isinstance(event, MarkPriceEvent):
                self._mark_price = event.mark_price
                self._last_context_receive_ns = event.header.receive_ts_ns
                self._last_context_wall_ns = self._last_frame_wall_ns
            elif isinstance(event, FundingEvent):
                self._funding_rate = event.funding_rate
                self._next_funding_ts_ns = event.next_funding_ts_ns
            market = self._assembler.observe(event)
            stale_trade_exclusions = self._assembler.stale_trade_exclusions
            self._paper_metrics.observe_stale_trade_exclusions(
                stale_trade_exclusions - self._observed_stale_trade_exclusions
            )
            self._observed_stale_trade_exclusions = stale_trade_exclusions
            if market is None:
                continue
            if self._engine is None:
                initial_mark = self._mark_price or (
                    market.bids[0].price + market.asks[0].price
                ) / Decimal("2")
                manifest = self._select_manifest(market.observed_ts_ns)
                self._engine = PaperTradingEngine(
                    manifest=manifest,
                    artifacts=self.artifacts,
                    risk_limits=self.bundle.settings.risk,
                    execution_config=self.bundle.settings.execution,
                    initial_equity_usd=self.bundle.settings.paper.initial_equity_usd,
                    initial_mark_price=initial_mark,
                    journal=self.journal,
                    kill_switch=self.kill_switch,
                    started_ts_ns=market.observed_ts_ns,
                    markout_horizon_ns=(self.bundle.settings.paper.markout_horizon_ms * 1_000_000),
                )
            self._engine.update_context(
                funding_rate=self._funding_rate,
                next_funding_ts_ns=self._next_funding_ts_ns,
            )
            started = time.perf_counter()
            context_fresh = (
                self._last_context_receive_ns is not None
                and market.observed_ts_ns - self._last_context_receive_ns
                <= self.bundle.settings.risk.public_data_stale_after_ms * 1_000_000
            )
            cycle = self._engine.on_market(
                market,
                mark_price=self._mark_price,
                feed_connected=context_fresh,
            )
            self._paper_metrics.observe_cycle(
                self._engine,
                cycle,
                latency_seconds=time.perf_counter() - started,
                initial_equity_usd=float(self.bundle.settings.paper.initial_equity_usd),
            )
            self._write_status(
                "degraded"
                if not context_fresh
                else ("ready" if cycle.features.ready else "warming")
            )

    async def run(self, stop: asyncio.Event) -> None:
        settings = self.bundle.settings
        self._write_status("starting")
        catalog_path = settings.storage.state_root / "market-data" / "raw-catalog.duckdb"
        network: Literal["mainnet", "testnet"] = (
            "mainnet" if settings.exchange.network is ExchangeNetwork.MAINNET else "testnet"
        )
        with ManifestCatalog(catalog_path) as catalog:
            recorder = MarketDataRecorder(
                websocket_url=str(settings.exchange.websocket_url),
                network=network,
                environment=settings.environment,
                config=settings.market_data,
                data_root=settings.storage.data_root,
                state_root=settings.storage.state_root,
                catalog=catalog,
                metrics=self._recorder_metrics,
                frame_consumer=self.consume_frame,
                socket_factory=self._socket_factory or default_socket_factory,
            )
            watchdog = asyncio.create_task(self._watchdog(stop))
            try:
                await recorder.run(stop)
            except Exception as exc:
                self._last_error_code = type(exc).__name__.lower()
                if self._engine is not None:
                    with contextlib.suppress(Exception):
                        self.journal.record_event(
                            self._engine.manifest.run_id,
                            ts_ns=self.clock_ns(),
                            kind="service_failure",
                            detail=self._last_error_code,
                        )
                self._write_status("failed")
                raise
            finally:
                stop.set()
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog
        self._recorder_connected = False
        self._write_status("stopped")

    def mark_stopped(self) -> None:
        """Publish a terminal status for finite retained-data replay."""

        self._recorder_connected = False
        self._write_status("stopped")

    async def _watchdog(self, stop: asyncio.Event) -> None:
        interval = self.bundle.settings.paper.watchdog_interval_ms / 1_000
        while not stop.is_set():
            await asyncio.sleep(interval)
            now = self.clock_ns()
            freshness_ns = min(
                self.bundle.settings.market_data.stale_after_seconds * 1_000_000_000,
                self.bundle.settings.risk.public_data_stale_after_ms * 1_000_000,
            )
            recent_frame = (
                self._last_frame_wall_ns is not None
                and now - self._last_frame_wall_ns <= freshness_ns
            )
            recent_context = (
                self._last_context_wall_ns is not None
                and now - self._last_context_wall_ns <= freshness_ns
            )
            connected = self._recorder_connected and recent_frame and recent_context
            self._recorder_connected = connected
            if self._engine is not None:
                self._engine.watchdog(now, recorder_connected=connected)
                if self._engine.resumed:
                    self._engine.confirm_restart_drill(now)
                self._paper_metrics.update_state(
                    self._engine,
                    initial_equity_usd=float(self.bundle.settings.paper.initial_equity_usd),
                )
                status: Literal["starting", "warming", "ready", "degraded", "stopped", "failed"] = (
                    "degraded"
                    if not connected or self.kill_switch.read().active
                    else ("ready" if self._engine.feature_ready else "warming")
                )
                self._write_status(status)
            else:
                self._write_status("starting" if connected else "degraded")

    def _select_manifest(self, started_ts_ns: int) -> PaperRunManifest:
        latest = self.journal.latest_manifest()
        same_identity = latest is not None and (
            latest.environment == self.bundle.settings.environment
            and latest.code_identity == self.code_identity
            and latest.config_fingerprint == self.bundle.fingerprint
            and latest.feature_config_sha256 == self.artifacts.feature_config_sha256
            and latest.strategy_config_sha256 == self.artifacts.strategy_config_sha256
            and latest.scenario_id == self.artifacts.scenario.scenario_id
            and latest.scenario_sha256 == self.artifacts.scenario.sha256()
            and latest.evidence_policy_sha256 == self.artifacts.evidence_policy_sha256
            and latest.strategy_id == self.artifacts.strategy_config.strategy_id
        )
        if same_identity:
            assert latest is not None
            return latest
        run_id = f"paper-{started_ts_ns}"
        return PaperRunManifest(
            run_id=run_id,
            started_ts_ns=started_ts_ns,
            environment=self.bundle.settings.environment,
            code_identity=self.code_identity,
            config_fingerprint=self.bundle.fingerprint,
            feature_config_sha256=self.artifacts.feature_config_sha256,
            strategy_config_sha256=self.artifacts.strategy_config_sha256,
            scenario_id=self.artifacts.scenario.scenario_id,
            scenario_sha256=self.artifacts.scenario.sha256(),
            evidence_policy_sha256=self.artifacts.evidence_policy_sha256,
            strategy_id=self.artifacts.strategy_config.strategy_id,
            credential_capability="none",
        )

    def _write_status(
        self,
        status: Literal["starting", "warming", "ready", "degraded", "stopped", "failed"],
    ) -> None:
        now = self.clock_ns()
        engine = self._engine
        run_id = "paper-awaiting-first-book" if engine is None else engine.manifest.run_id
        payload = PaperRuntimeStatus(
            status=status,
            run_id=run_id,
            environment=self.bundle.settings.environment,
            heartbeat_ts_ns=now,
            last_public_data_ts_ns=(None if engine is None else engine.last_public_data_ts_ns),
            feed_connected=(self._recorder_connected and (engine is None or engine.feed_connected)),
            feature_ready=False if engine is None else engine.feature_ready,
            operator_kill=self.kill_switch.read().active,
            scenario_id=self.artifacts.scenario.scenario_id,
            scenario_sha256=self.artifacts.scenario.sha256(),
            calibration_state=self.artifacts.scenario.calibration_state,
            strategy_id=self.artifacts.strategy_config.strategy_id,
            config_fingerprint=self.bundle.fingerprint,
            account=None if engine is None else engine.simulator.account,
            open_orders=0 if engine is None else len(engine.simulator.open_orders),
            decisions=0 if engine is None else engine.decision_count,
            fills=0 if engine is None else engine.fill_count,
            last_error_code=self._last_error_code,
        )
        atomic_replace_bytes(self.status_path, payload.canonical_bytes() + b"\n")
