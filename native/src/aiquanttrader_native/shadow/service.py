"""Network-isolated production kernel/risk path over durable public ingress."""

from __future__ import annotations

import asyncio
import contextlib
import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import DeploymentMode
from aiquanttrader_native.domain.market import FundingEvent, MarkPriceEvent
from aiquanttrader_native.market_data.io import atomic_replace_bytes
from aiquanttrader_native.market_data.protocol import ParsedFrame
from aiquanttrader_native.paper.engine import PaperTradingEngine
from aiquanttrader_native.paper.journal import PaperJournal
from aiquanttrader_native.paper.market import LiveMarketStateAssembler
from aiquanttrader_native.paper.models import PaperRunManifest
from aiquanttrader_native.risk.kill_switch import KillSwitchStore
from aiquanttrader_native.shadow.audit import ShadowAuditJournal
from aiquanttrader_native.shadow.config import ShadowArtifacts
from aiquanttrader_native.shadow.ingress import ShadowIngressReader, ShadowIngressRecord
from aiquanttrader_native.shadow.metrics import ShadowMetrics
from aiquanttrader_native.shadow.models import ShadowRuntimeStatus
from aiquanttrader_native.shadow.sink import ShadowCommandSink


class ShadowClockError(RuntimeError):
    pass


class ShadowEngineService:
    def __init__(
        self,
        *,
        bundle: ConfigBundle,
        artifacts: ShadowArtifacts,
        ingress: ShadowIngressReader,
        journal: PaperJournal,
        audit: ShadowAuditJournal,
        kill_switch: KillSwitchStore,
        code_identity: str,
        image_identity: str,
        metrics: ShadowMetrics,
        replay_mode: bool = False,
        start_sequence: int | None = None,
        status_path: Path | None = None,
    ) -> None:
        settings = bundle.settings
        if settings.mode is not DeploymentMode.SHADOW or not settings.shadow.enabled:
            raise ValueError("shadow engine requires the enabled shadow environment")
        if (
            settings.execution.enabled
            or settings.sentinel.enabled
            or any(
                value is not None
                for value in (
                    settings.exchange.account_address,
                    settings.exchange.trading_wallet_secret_path,
                    settings.exchange.control_wallet_secret_path,
                )
            )
        ):
            raise ValueError("shadow engine refuses exchange execution capability")
        self.bundle = bundle
        self.artifacts = artifacts
        self.ingress = ingress
        self.journal = journal
        self.audit = audit
        self.kill_switch = kill_switch
        self.code_identity = code_identity
        self.image_identity = image_identity
        self.metrics = metrics
        self.replay_mode = replay_mode
        if start_sequence is not None and start_sequence < 0:
            raise ValueError("shadow start sequence cannot be negative")
        self._requested_start_sequence = start_sequence
        self._run_start_sequence = 0
        self.status_path = (
            settings.storage.state_root / "shadow" / "status.json"
            if status_path is None
            else status_path.resolve()
        )
        self._assembler = LiveMarketStateAssembler(
            depth_levels=artifacts.paper.feature_config.depth_levels
        )
        self._engine: PaperTradingEngine | None = None
        self._sink = ShadowCommandSink()
        self._mark_price: Decimal | None = None
        self._funding_rate = Decimal("0")
        self._next_funding_ts_ns: int | None = None
        self._last_context_receive_ns: int | None = None
        self._last_ingress_receive_ns: int | None = None
        self._last_ingress_lag_ns: int | None = None
        self._last_error_code: str | None = None
        self._last_health_sample_ns = 0
        self._cursor = self._initial_cursor()

    @property
    def engine(self) -> PaperTradingEngine | None:
        return self._engine

    @property
    def cursor(self) -> int:
        return self._cursor

    def _initial_cursor(self) -> int:
        latest = self.journal.latest_manifest()
        if latest is not None and self._manifest_matches(latest):
            self._run_start_sequence = latest.source_start_sequence
            checkpoint = self.journal.latest_checkpoint(latest.run_id)
            return (
                latest.source_start_sequence
                if checkpoint is None or checkpoint.source_sequence is None
                else checkpoint.source_sequence
            )
        boundary = self._requested_start_sequence
        if boundary is None:
            boundary = 0 if self.replay_mode else self.ingress.latest_sequence()
        if boundary > self.ingress.latest_sequence():
            raise ValueError("shadow start sequence exceeds retained ingress")
        self._run_start_sequence = boundary
        return boundary

    async def consume_record(self, record: ShadowIngressRecord) -> None:
        expected = self._cursor + 1
        if record.sequence != expected:
            raise ValueError(
                f"shadow engine expected ingress sequence {expected}, observed {record.sequence}"
            )
        now = record.envelope.written_ts_ns if self.replay_mode else time.time_ns()
        future_ns = record.envelope.written_ts_ns - now
        maximum_skew_ns = self.bundle.settings.shadow.maximum_clock_skew_ms * 1_000_000
        if future_ns > maximum_skew_ns:
            raise ShadowClockError(
                f"gateway clock leads isolated engine by {future_ns}ns, beyond policy"
            )
        ingress_lag_ns = max(0, now - record.envelope.receive_ts_ns)
        self._last_ingress_receive_ns = record.envelope.receive_ts_ns
        self._last_ingress_lag_ns = ingress_lag_ns
        frame = ParsedFrame(
            channel=record.envelope.channel,
            events=record.envelope.events,
            is_control=record.envelope.is_control,
        )
        if frame.is_control:
            self._cursor = record.sequence
            return
        for event in frame.events:
            if isinstance(event, MarkPriceEvent):
                self._mark_price = event.mark_price
                self._last_context_receive_ns = event.header.receive_ts_ns
            elif isinstance(event, FundingEvent):
                self._funding_rate = event.funding_rate
                self._next_funding_ts_ns = event.next_funding_ts_ns
            market = self._assembler.observe(event)
            if market is None:
                continue
            if self._engine is None:
                initial_mark = self._mark_price or (
                    market.bids[0].price + market.asks[0].price
                ) / Decimal("2")
                manifest = self._select_manifest(market.observed_ts_ns)
                self._engine = PaperTradingEngine(
                    manifest=manifest,
                    artifacts=self.artifacts.paper,
                    risk_limits=self.bundle.settings.risk,
                    execution_config=self.bundle.settings.execution,
                    initial_equity_usd=self.bundle.settings.shadow.initial_equity_usd,
                    initial_mark_price=initial_mark,
                    journal=self.journal,
                    kill_switch=self.kill_switch,
                    started_ts_ns=market.observed_ts_ns,
                    markout_horizon_ns=(self.bundle.settings.shadow.markout_horizon_ms * 1_000_000),
                )
                self.audit.begin_run(manifest, self.image_identity)
                stats = self.journal.statistics(manifest.run_id)
                self._sink = ShadowCommandSink(restored_commands=stats.commands)
            self._engine.update_context(
                funding_rate=self._funding_rate,
                next_funding_ts_ns=self._next_funding_ts_ns,
            )
            maximum_lag_ns = self.bundle.settings.shadow.maximum_ingress_lag_ms * 1_000_000
            context_fresh = (
                self._last_context_receive_ns is not None
                and market.observed_ts_ns - self._last_context_receive_ns
                <= self.bundle.settings.risk.public_data_stale_after_ms * 1_000_000
            )
            ingress_fresh = ingress_lag_ns <= maximum_lag_ns
            started = time.perf_counter_ns()
            cycle = self._engine.on_market(
                market,
                mark_price=self._mark_price,
                feed_connected=context_fresh and ingress_fresh,
                source_sequence=record.sequence,
            )
            cycle_latency_ns = time.perf_counter_ns() - started
            self._sink.accept(cycle)
            completed = record.envelope.written_ts_ns if self.replay_mode else time.time_ns()
            total_ingress_ns = max(0, completed - record.envelope.receive_ts_ns)
            self.audit.record_cycle(
                self._engine.manifest.run_id,
                source_sequence=record.sequence,
                completed_ts_ns=completed,
                ingress_latency_ns=total_ingress_ns,
                cycle_latency_ns=cycle_latency_ns,
                feature_sha256=cycle.features.sha256(),
                decisions=len(cycle.decisions),
                commands=len(cycle.commands),
            )
            self.metrics.observe_cycle(
                self._engine,
                cycle,
                ingress_sequence=record.sequence,
                ingress_latency_seconds=total_ingress_ns / 1_000_000_000,
                cycle_latency_seconds=cycle_latency_ns / 1_000_000_000,
            )
        self._cursor = record.sequence
        self._write_status(self._current_status())
        self.metrics.publish()

    async def run(self, stop: asyncio.Event) -> None:
        self._write_status("starting")
        poll = self.bundle.settings.shadow.ingress_poll_interval_ms / 1_000
        watchdog_interval_ns = self.bundle.settings.shadow.watchdog_interval_ms * 1_000_000
        next_watchdog_ns = time.monotonic_ns() + watchdog_interval_ns
        try:
            while not stop.is_set():
                records = self.ingress.read_after(
                    self._cursor,
                    limit=self.bundle.settings.shadow.ingress_batch_size,
                )
                for record in records:
                    await self.consume_record(record)
                monotonic = time.monotonic_ns()
                if monotonic >= next_watchdog_ns:
                    self._watchdog(time.time_ns())
                    next_watchdog_ns = monotonic + watchdog_interval_ns
                if not records:
                    await asyncio.sleep(poll)
        except Exception as exc:
            self._last_error_code = type(exc).__name__.lower()
            if self._engine is not None:
                with contextlib.suppress(Exception):
                    self.audit.record_failure(
                        self._engine.manifest.run_id,
                        failed_ts_ns=time.time_ns(),
                        kind="service_failure",
                        detail=self._last_error_code,
                    )
            self._write_status("failed")
            self.metrics.publish()
            raise
        self._write_status("stopped")
        self.metrics.publish()

    def _watchdog(self, now: int) -> None:
        maximum_lag_ns = self.bundle.settings.shadow.maximum_ingress_lag_ms * 1_000_000
        lag = (
            None
            if self._last_ingress_receive_ns is None
            else max(0, now - self._last_ingress_receive_ns)
        )
        connected = lag is not None and lag <= maximum_lag_ns
        if self._engine is not None:
            self._engine.watchdog(now, recorder_connected=connected)
            self._sink.command_count = self.journal.statistics(
                self._engine.manifest.run_id
            ).commands
            self.metrics.update_state(self._engine, ingress_sequence=self._cursor)
            interval_ns = self.bundle.settings.shadow.health_sample_interval_ms * 1_000_000
            if now - self._last_health_sample_ns >= interval_ns:
                healthy = (
                    connected and not self.kill_switch.read().active and self._engine.feature_ready
                )
                self.audit.record_health(
                    self._engine.manifest.run_id,
                    sample_ts_ns=now,
                    healthy=healthy,
                    ingress_sequence=self._cursor,
                    ingress_lag_ns=lag,
                )
                self._last_health_sample_ns = now
        self._last_ingress_lag_ns = lag
        self._write_status(self._current_status())
        self.metrics.publish()

    def _select_manifest(self, started_ts_ns: int) -> PaperRunManifest:
        latest = self.journal.latest_manifest()
        if latest is not None and self._manifest_matches(latest):
            return latest
        return PaperRunManifest(
            run_id=(
                f"shadow-replay-{started_ts_ns}" if self.replay_mode else f"shadow-{started_ts_ns}"
            ),
            environment=self.bundle.settings.environment,
            started_ts_ns=started_ts_ns,
            code_identity=self.code_identity,
            image_identity=self.image_identity,
            config_fingerprint=self.bundle.fingerprint,
            feature_config_sha256=self.artifacts.paper.feature_config_sha256,
            strategy_config_sha256=self.artifacts.paper.strategy_config_sha256,
            scenario_id=self.artifacts.paper.scenario.scenario_id,
            scenario_sha256=self.artifacts.paper.scenario.sha256(),
            evidence_policy_sha256=self.artifacts.paper.evidence_policy_sha256,
            strategy_id=self.artifacts.paper.strategy_config.strategy_id,
            source_start_sequence=self._run_start_sequence,
        )

    def _manifest_matches(self, manifest: PaperRunManifest) -> bool:
        return (
            manifest.environment == self.bundle.settings.environment
            and manifest.code_identity == self.code_identity
            and manifest.image_identity == self.image_identity
            and manifest.config_fingerprint == self.bundle.fingerprint
            and manifest.feature_config_sha256 == self.artifacts.paper.feature_config_sha256
            and manifest.strategy_config_sha256 == self.artifacts.paper.strategy_config_sha256
            and manifest.scenario_sha256 == self.artifacts.paper.scenario.sha256()
            and manifest.evidence_policy_sha256 == self.artifacts.paper.evidence_policy_sha256
            and manifest.strategy_id == self.artifacts.paper.strategy_config.strategy_id
            and (
                self._requested_start_sequence is None
                or manifest.source_start_sequence == self._requested_start_sequence
            )
        )

    def _current_status(
        self,
    ) -> Literal["starting", "warming", "ready", "degraded", "stopped", "failed"]:
        engine = self._engine
        if engine is None:
            return "starting"
        if not engine.feed_connected or self.kill_switch.read().active:
            return "degraded"
        return "ready" if engine.feature_ready else "warming"

    def _write_status(
        self,
        status: Literal["starting", "warming", "ready", "degraded", "stopped", "failed"],
    ) -> None:
        now = time.time_ns()
        engine = self._engine
        payload = ShadowRuntimeStatus(
            status=status,
            run_id="shadow-awaiting-first-book" if engine is None else engine.manifest.run_id,
            heartbeat_ts_ns=now,
            last_public_data_ts_ns=(None if engine is None else engine.last_public_data_ts_ns),
            last_ingress_sequence=self._cursor,
            ingress_lag_ns=self._last_ingress_lag_ns,
            feed_connected=False if engine is None else engine.feed_connected,
            feature_ready=False if engine is None else engine.feature_ready,
            operator_kill=self.kill_switch.read().active,
            strategy_id=self.artifacts.paper.strategy_config.strategy_id,
            scenario_id=self.artifacts.paper.scenario.scenario_id,
            scenario_sha256=self.artifacts.paper.scenario.sha256(),
            calibration_state=self.artifacts.paper.scenario.calibration_state,
            config_fingerprint=self.bundle.fingerprint,
            image_identity=self.image_identity,
            account=None if engine is None else engine.simulator.account,
            open_orders=0 if engine is None else len(engine.simulator.open_orders),
            decisions=0 if engine is None else engine.decision_count,
            commands=self._sink.command_count,
            fills=0 if engine is None else engine.fill_count,
            last_error_code=self._last_error_code,
        )
        atomic_replace_bytes(self.status_path, payload.canonical_bytes() + b"\n")
