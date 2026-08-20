"""Production strategy and risk path terminating in the paper exchange simulator."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aiquanttrader.backtest.kernel import KernelDecision, KernelMarketState
from aiquanttrader.config.models import ExecutionConfig, RiskLimits
from aiquanttrader.domain.execution import OrderIntent, RiskReason, RiskSnapshot, RiskState
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.engine import IncrementalFeatureEngine
from aiquanttrader.features.market_structure import (
    CausalMarketStructureEngine,
    SmartMoneySnapshot,
)
from aiquanttrader.features.models import InventoryState, MicrostructureSnapshot
from aiquanttrader.paper.config import PaperArtifacts
from aiquanttrader.paper.drift import PaperDriftMonitor
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.models import (
    PaperCommandKind,
    PaperDecisionRecord,
    PaperEngineCheckpoint,
    PaperExecutionCommand,
    PaperFill,
    PaperMarkout,
    PaperOrder,
    PaperRunManifest,
)
from aiquanttrader.paper.simulator import PaperExchangeSimulator, SimulatorUpdate
from aiquanttrader.research.models import DriftReport
from aiquanttrader.risk.authority import RiskAuthority
from aiquanttrader.risk.kill_switch import KillSwitchStore
from aiquanttrader.strategies.adaptive_scalper import (
    AdaptiveForecastState,
    AdaptiveScalperConfig,
    AdaptiveScalperKernel,
    AdaptiveScalperMemory,
)
from aiquanttrader.strategies.common import StrategyInput
from aiquanttrader.strategies.market_maker import (
    AvellanedaStoikovConfig,
    AvellanedaStoikovKernel,
    MarketMakerMemory,
)
from aiquanttrader.strategies.scalper import (
    OrderFlowScalperConfig,
    OrderFlowScalperKernel,
    ScalperMemory,
)
from aiquanttrader.strategies.smart_money_scalper import (
    SmartMoneyScalperConfig,
    SmartMoneyScalperKernel,
    SmartMoneyScalperMemory,
)

FUNDING_INTERVAL_NS = 3_600_000_000_000


@dataclass(frozen=True, slots=True)
class PaperEngineCycle:
    features: MicrostructureSnapshot
    market_structure: SmartMoneySnapshot | None
    strategy_decision: KernelDecision
    decisions: tuple[PaperDecisionRecord, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    markouts: tuple[PaperMarkout, ...]
    drift_report: DriftReport | None
    risk_state: RiskState
    risk_reasons: tuple[RiskReason, ...]
    commands: tuple[PaperExecutionCommand, ...]


@dataclass(frozen=True, slots=True)
class PaperWatchdogUpdate:
    orders: tuple[PaperOrder, ...] = ()
    fills: tuple[PaperFill, ...] = ()
    markouts: tuple[PaperMarkout, ...] = ()


class PaperTradingEngine:
    """Apply the exact research kernels and hard risk authority to live public state."""

    def __init__(
        self,
        *,
        manifest: PaperRunManifest,
        artifacts: PaperArtifacts,
        risk_limits: RiskLimits,
        execution_config: ExecutionConfig,
        initial_equity_usd: Decimal,
        initial_mark_price: Decimal,
        journal: PaperJournal,
        kill_switch: KillSwitchStore,
        started_ts_ns: int,
        markout_horizon_ns: int,
    ) -> None:
        self.manifest = manifest
        self.artifacts = artifacts
        self.journal = journal
        self.kill_switch = kill_switch
        self._risk_limits = risk_limits
        self._markout_horizon_ns = markout_horizon_ns
        self._now_ns = started_ts_ns
        self._last_market: KernelMarketState | None = None
        self._feed_connected = True
        self._feature_engine = IncrementalFeatureEngine(artifacts.feature_config)
        restored_account = journal.latest_account(manifest.run_id)
        restored_orders = journal.restore_open_orders(manifest.run_id)
        checkpoint = journal.latest_checkpoint(manifest.run_id)
        restored_structure = journal.latest_market_structure_state(manifest.run_id)
        self._structure_engine = CausalMarketStructureEngine(restored_state=restored_structure)
        self._persisted_structure_revision = (
            -1 if restored_structure is None else restored_structure.revision
        )
        self._last_structure: SmartMoneySnapshot | None = None
        self._last_strategy_decision = KernelDecision()
        self._last_watchdog_update = PaperWatchdogUpdate()
        self.simulator = PaperExchangeSimulator(
            artifacts.scenario,
            initial_equity_usd=initial_equity_usd,
            initial_mark_price=initial_mark_price,
            started_ts_ns=started_ts_ns,
            restored_account=restored_account,
            restored_orders=restored_orders,
            identity_namespace=manifest.run_id,
        )
        resumed = journal.begin_run(manifest, self.simulator.account)
        self.resumed = resumed
        self._command_sequence = journal.next_command_sequence(manifest.run_id)
        policy = artifacts.evidence_policy
        self._drift_monitor = PaperDriftMonitor(
            policy,
            restored_vectors=journal.feature_vectors(
                manifest.run_id,
                baseline_samples=policy.drift_baseline_samples,
                current_samples=policy.drift_window_samples,
            ),
        )
        self._kernel, self._memory = self._build_strategy(checkpoint)
        self._sequence = 0 if checkpoint is None else checkpoint.sequence
        self._last_independent_ts_ns = (
            None if checkpoint is None else checkpoint.last_independent_decision_ts_ns
        )
        self._funding_rate = Decimal("0") if checkpoint is None else checkpoint.funding_rate
        self._next_funding_ns = (
            None if checkpoint is None else checkpoint.next_funding_settlement_ns
        )
        self._source_sequence = None if checkpoint is None else checkpoint.source_sequence
        self._pending_markouts: dict[str, PaperFill] = {
            fill.fill_id: fill
            for fill in journal.pending_markout_fills(manifest.run_id, markout_horizon_ns)
        }
        self._authority = RiskAuthority(
            risk_limits,
            execution_config,
            kill_switch=kill_switch,
            inflight_count=lambda: 0,
            clock_ns=lambda: self._now_ns,
        )
        if resumed:
            pending = self.simulator.request_cancel_all(requested_ts_ns=started_ts_ns)
            commands = self._cancel_commands(pending, PaperCommandKind.CANCEL_ALL, started_ts_ns)
            self.journal.record_cycle(
                manifest.run_id,
                orders=pending,
                fills=(),
                account=self.simulator.account,
                commands=commands,
            )
            self.journal.record_event(
                manifest.run_id,
                ts_ns=started_ts_ns,
                kind="restart_reconciliation",
                detail=f"restored and cancel-requested {len(pending)} open paper orders",
            )
        statistics = journal.statistics(manifest.run_id)
        self._decision_count = statistics.approved_decisions + statistics.denied_decisions
        self._fill_count = statistics.fills

    @property
    def feature_ready(self) -> bool:
        if isinstance(
            self.artifacts.strategy_config,
            (SmartMoneyScalperConfig, AdaptiveScalperConfig),
        ):
            return self._feature_engine.ready and self._structure_engine.ready
        return self._feature_engine.ready

    @property
    def adaptive_forecast(self) -> AdaptiveForecastState | None:
        if isinstance(self._memory, AdaptiveScalperMemory):
            return self._memory.forecast
        return None

    @property
    def feed_connected(self) -> bool:
        return self._feed_connected

    @property
    def last_public_data_ts_ns(self) -> int | None:
        return None if self._last_market is None else self._last_market.observed_ts_ns

    @property
    def decision_count(self) -> int:
        return self._decision_count

    @property
    def fill_count(self) -> int:
        return self._fill_count

    @property
    def last_market_structure(self) -> SmartMoneySnapshot | None:
        return self._last_structure

    @property
    def last_strategy_decision(self) -> KernelDecision:
        return self._last_strategy_decision

    @property
    def last_watchdog_update(self) -> PaperWatchdogUpdate:
        return self._last_watchdog_update

    def update_context(
        self,
        *,
        funding_rate: Decimal | None = None,
        next_funding_ts_ns: int | None = None,
    ) -> None:
        if funding_rate is not None:
            self._funding_rate = funding_rate
        if next_funding_ts_ns is not None and (
            self._next_funding_ns is None or next_funding_ts_ns > self._next_funding_ns
        ):
            self._next_funding_ns = next_funding_ts_ns

    def on_market(
        self,
        market: KernelMarketState,
        *,
        mark_price: Decimal | None = None,
        feed_connected: bool = True,
        source_sequence: int | None = None,
    ) -> PaperEngineCycle:
        if source_sequence is not None and (
            self._source_sequence is not None and source_sequence <= self._source_sequence
        ):
            raise ValueError("paper source sequences must be strictly increasing")
        if (
            self._last_market is not None
            and market.observed_ts_ns <= self._last_market.observed_ts_ns
        ):
            raise ValueError("paper market states must be strictly increasing")
        self._now_ns = market.observed_ts_ns
        self._last_market = market
        self._feed_connected = feed_connected
        self._source_sequence = source_sequence
        self._settle_funding_if_due(mark_price or self.simulator.account.mark_price)
        simulation = self.simulator.advance(market, mark_price=mark_price)
        for fill in simulation.fills:
            self._pending_markouts[fill.fill_id] = fill
        self._synchronize_memory(market.observed_ts_ns)
        markouts = self._resolve_markouts(simulation.account.mark_price, market.observed_ts_ns)

        risk_snapshot = self._risk_snapshot()
        risk_state, risk_reasons = self._authority.state(risk_snapshot)
        changed_orders: list[PaperOrder] = list(simulation.orders)
        commands: list[PaperExecutionCommand] = []
        if risk_state is not RiskState.ACTIVE:
            risk_canceled = self.simulator.request_cancel_all(requested_ts_ns=market.observed_ts_ns)
            changed_orders.extend(risk_canceled)
            commands.extend(
                self._cancel_commands(
                    risk_canceled, PaperCommandKind.CANCEL_ALL, market.observed_ts_ns
                )
            )
            self._record_economic_drills(risk_reasons, market.observed_ts_ns)

        inventory = self._inventory_state(simulation.account.position_base)
        features = self._feature_engine.update(market, inventory=inventory)
        market_structure = self._structure_engine.update(market)
        self._last_structure = market_structure
        if self._structure_engine.state.revision != self._persisted_structure_revision:
            self.journal.record_market_structure_state(
                self.manifest.run_id,
                self._structure_engine.state,
            )
            self._persisted_structure_revision = self._structure_engine.state.revision
        drift_report = self._drift_monitor.update(features)
        transition = self._kernel.decide(
            StrategyInput(
                features=features,
                funding_rate=self._funding_rate,
                estimated_maker_fee_bps=max(Decimal("0"), self.artifacts.scenario.maker_fee_bps),
                estimated_taker_fee_bps=max(Decimal("0"), self.artifacts.scenario.taker_fee_bps),
                estimated_slippage_bps=self.artifacts.scenario.taker_slippage_bps,
                market_structure=market_structure,
                position_average_entry_price=simulation.account.average_entry_price,
                position_opened_ts_ns=(
                    self._memory.position_opened_ts_ns
                    if isinstance(
                        self._memory,
                        (SmartMoneyScalperMemory, AdaptiveScalperMemory),
                    )
                    else None
                ),
            ),
            self._memory,
        )
        self._memory = transition.memory
        self._last_strategy_decision = transition.decision
        for intent_id in transition.decision.cancel_intent_ids:
            canceled_order = self.simulator.request_cancel(
                intent_id, requested_ts_ns=market.observed_ts_ns
            )
            if canceled_order is not None:
                changed_orders.append(canceled_order)
                commands.append(
                    self._cancel_command(
                        canceled_order,
                        PaperCommandKind.CANCEL,
                        market.observed_ts_ns,
                    )
                )

        records: list[PaperDecisionRecord] = []
        for intent in transition.decision.submit:
            snapshot = self._risk_snapshot()
            decision = self._authority.evaluate(intent, snapshot)
            independent = self._is_independent(intent.created_ts_ns)
            record_identity = hashlib.sha256(
                f"{manifest_identity(self.manifest)}:{decision.decision_id}".encode()
            ).hexdigest()[:32]
            record = PaperDecisionRecord(
                record_id=f"decision-{record_identity}",
                sequence=self._sequence,
                decision_ts_ns=market.observed_ts_ns,
                feature_snapshot_sha256=features.sha256(),
                strategy_id=intent.strategy_id,
                intent=intent,
                risk_decision=decision,
                independent=independent,
            )
            self._sequence += 1
            records.append(record)
            if decision.allowed:
                self._authority.consume(decision, intent, snapshot)
                commands.append(
                    self._submit_command(
                        intent=intent,
                        risk_decision_id=decision.decision_id,
                        feature_snapshot_sha256=features.sha256(),
                        command_ts_ns=market.observed_ts_ns,
                    )
                )
                changed_orders.append(
                    self.simulator.submit(intent, accepted_ts_ns=market.observed_ts_ns)
                )

        checkpoint = self._checkpoint(market.observed_ts_ns)
        self.journal.record_engine_cycle(
            self.manifest.run_id,
            orders=changed_orders,
            fills=simulation.fills,
            account=self.simulator.account,
            feature=features,
            decisions=records,
            markouts=markouts,
            checkpoint=checkpoint,
            drift_report=drift_report,
            commands=commands,
        )
        self._decision_count += len(records)
        self._fill_count += len(simulation.fills)
        return PaperEngineCycle(
            features=features,
            market_structure=market_structure,
            strategy_decision=transition.decision,
            decisions=tuple(records),
            orders=tuple(changed_orders),
            fills=simulation.fills,
            markouts=markouts,
            drift_report=drift_report,
            risk_state=risk_state,
            risk_reasons=risk_reasons,
            commands=tuple(commands),
        )

    def watchdog(self, now_ts_ns: int, *, recorder_connected: bool) -> tuple[RiskReason, ...]:
        self._now_ns = now_ts_ns
        self._feed_connected = recorder_connected
        self._last_watchdog_update = PaperWatchdogUpdate()
        stale = (
            self._last_market is None
            or now_ts_ns - self._last_market.observed_ts_ns
            > self._risk_limits.public_data_stale_after_ms * 1_000_000
        )
        kill_active = self.kill_switch.read().active
        activation = SimulatorUpdate((), (), self.simulator.account)
        if not stale and recorder_connected and not kill_active and self._last_market is not None:
            activation = self.simulator.activate_pending(now_ts_ns, self._last_market)
            for fill in activation.fills:
                self._pending_markouts[fill.fill_id] = fill
            self._synchronize_memory(now_ts_ns)
        elapsed = self.simulator.elapse(now_ts_ns)
        requested: tuple[PaperOrder, ...] = ()
        if stale or not recorder_connected or kill_active:
            requested = self.simulator.request_cancel_all(requested_ts_ns=now_ts_ns)
        markouts: tuple[PaperMarkout, ...] = ()
        changed = (*activation.orders, *elapsed, *requested)
        if changed or activation.fills or markouts:
            commands = self._cancel_commands(requested, PaperCommandKind.CANCEL_ALL, now_ts_ns)
            self.journal.record_cycle(
                self.manifest.run_id,
                orders=changed,
                fills=activation.fills,
                account=self.simulator.account,
                commands=commands,
            )
            self.journal.record_checkpoint(self._checkpoint(now_ts_ns))
            self._fill_count += len(activation.fills)
            self._last_watchdog_update = PaperWatchdogUpdate(
                orders=changed,
                fills=activation.fills,
                markouts=markouts,
            )
        if self._last_market is None:
            return ()
        state, reasons = self._authority.state(self._risk_snapshot(snapshot_ts_ns=now_ts_ns))
        if not self.simulator.open_orders:
            self._record_economic_drills(reasons, now_ts_ns)
            if kill_active and state is RiskState.HALTED:
                self.journal.record_drill(
                    self.manifest.run_id,
                    "operator_kill",
                    ts_ns=now_ts_ns,
                    evidence="risk authority halted and all paper orders were canceled",
                )
            if stale and RiskReason.PUBLIC_DATA_STALE in reasons:
                self.journal.record_drill(
                    self.manifest.run_id,
                    "stale_data",
                    ts_ns=now_ts_ns,
                    evidence=(
                        "risk authority entered cancel-only and all paper orders were canceled"
                    ),
                )
        return reasons

    def confirm_restart_drill(self, now_ts_ns: int) -> None:
        if not self.simulator.open_orders:
            self.journal.record_drill(
                self.manifest.run_id,
                "restart",
                ts_ns=now_ts_ns,
                evidence=(
                    "restored account and strategy checkpoint with zero unreconciled open orders"
                ),
            )

    def _risk_snapshot(self, *, snapshot_ts_ns: int | None = None) -> RiskSnapshot:
        now = self._now_ns if snapshot_ts_ns is None else snapshot_ts_ns
        account = self.simulator.account
        buys, sells = self.simulator.pending_exposure()
        equity = max(account.equity_usd, Decimal("0.00000001"))
        leverage = abs(account.position_base * account.mark_price) / equity
        public_ts = now if self._last_market is None else self._last_market.observed_ts_ns
        return RiskSnapshot(
            snapshot_ts_ns=now,
            public_data_ts_ns=public_ts,
            private_data_ts_ns=min(account.updated_ts_ns, now),
            mark_price=account.mark_price,
            position_base=account.position_base,
            pending_buy_base=buys,
            pending_sell_base=sells,
            account_equity_usd=equity,
            day_start_equity_usd=account.day_start_equity_usd,
            high_water_equity_usd=account.high_water_equity_usd,
            leverage=leverage,
            open_order_count=len(self.simulator.open_orders),
            exchange_connected=self._feed_connected,
            reconciliation_complete=True,
            operator_kill=self.kill_switch.read().active,
        )

    def _inventory_state(self, position_base: Decimal) -> InventoryState:
        equity = max(self.simulator.account.equity_usd, Decimal("0.00000001"))
        utilization = min(
            Decimal("1"),
            abs(position_base * self.simulator.account.mark_price)
            / equity
            / self._risk_limits.max_leverage,
        )
        return InventoryState(
            confirmed_base=position_base,
            margin_utilization=utilization,
        )

    def _build_strategy(
        self, checkpoint: PaperEngineCheckpoint | None
    ) -> tuple[
        Any,
        MarketMakerMemory | ScalperMemory | SmartMoneyScalperMemory | AdaptiveScalperMemory,
    ]:
        config = self.artifacts.strategy_config
        if isinstance(config, AvellanedaStoikovConfig):
            memory = (
                MarketMakerMemory()
                if checkpoint is None
                else MarketMakerMemory.model_validate_json(checkpoint.strategy_memory_json)
            )
            return AvellanedaStoikovKernel(config), memory
        if isinstance(config, OrderFlowScalperConfig):
            scalper_memory = (
                ScalperMemory()
                if checkpoint is None
                else ScalperMemory.model_validate_json(checkpoint.strategy_memory_json)
            )
            return OrderFlowScalperKernel(config), scalper_memory
        if isinstance(config, SmartMoneyScalperConfig):
            smart_memory = (
                SmartMoneyScalperMemory()
                if checkpoint is None
                else SmartMoneyScalperMemory.model_validate_json(checkpoint.strategy_memory_json)
            )
            return SmartMoneyScalperKernel(config), smart_memory
        if isinstance(config, AdaptiveScalperConfig):
            adaptive_memory = (
                AdaptiveScalperMemory()
                if checkpoint is None
                else AdaptiveScalperMemory.model_validate_json(checkpoint.strategy_memory_json)
            )
            return AdaptiveScalperKernel(config), adaptive_memory
        raise TypeError("unsupported paper strategy configuration")

    def _checkpoint(self, ts_ns: int) -> PaperEngineCheckpoint:
        return PaperEngineCheckpoint(
            run_id=self.manifest.run_id,
            sequence=self._sequence,
            checkpoint_ts_ns=ts_ns,
            strategy_id=self.artifacts.strategy_config.strategy_id,
            strategy_memory_json=self._memory.model_dump_json(),
            last_independent_decision_ts_ns=self._last_independent_ts_ns,
            funding_rate=self._funding_rate,
            next_funding_settlement_ns=self._next_funding_ns,
            source_sequence=self._source_sequence,
        )

    def _synchronize_memory(self, observed_ts_ns: int) -> None:
        account = self.simulator.account
        if isinstance(self._memory, (SmartMoneyScalperMemory, AdaptiveScalperMemory)):
            self._memory = self._memory.synchronize_position(
                account.position_base,
                account.average_entry_price,
                observed_ts_ns,
            )
        else:
            self._memory = self._memory.with_inventory(account.position_base)

    def _submit_command(
        self,
        *,
        intent: OrderIntent,
        risk_decision_id: str,
        feature_snapshot_sha256: str,
        command_ts_ns: int,
    ) -> PaperExecutionCommand:
        sequence = self._command_sequence
        self._command_sequence += 1
        identity = hashlib.sha256(
            f"{manifest_identity(self.manifest)}:{sequence}:submit:{intent.intent_id}".encode()
        ).hexdigest()[:32]
        return PaperExecutionCommand(
            command_id=f"command-{identity}",
            sequence=sequence,
            command_ts_ns=command_ts_ns,
            kind=PaperCommandKind.SUBMIT,
            intent_id=intent.intent_id,
            strategy_id=intent.strategy_id,
            intent=intent,
            risk_decision_id=risk_decision_id,
            feature_snapshot_sha256=feature_snapshot_sha256,
            source_sequence=self._source_sequence,
        )

    def _cancel_command(
        self,
        order: PaperOrder,
        kind: PaperCommandKind,
        command_ts_ns: int,
    ) -> PaperExecutionCommand:
        sequence = self._command_sequence
        self._command_sequence += 1
        identity = hashlib.sha256(
            f"{manifest_identity(self.manifest)}:{sequence}:{kind.value}:{order.intent.intent_id}".encode()
        ).hexdigest()[:32]
        return PaperExecutionCommand(
            command_id=f"command-{identity}",
            sequence=sequence,
            command_ts_ns=command_ts_ns,
            kind=kind,
            intent_id=order.intent.intent_id,
            strategy_id=order.intent.strategy_id,
            source_sequence=self._source_sequence,
        )

    def _cancel_commands(
        self,
        orders: tuple[PaperOrder, ...],
        kind: PaperCommandKind,
        command_ts_ns: int,
    ) -> tuple[PaperExecutionCommand, ...]:
        return tuple(self._cancel_command(order, kind, command_ts_ns) for order in orders)

    def _is_independent(self, ts_ns: int) -> bool:
        previous = self._last_independent_ts_ns
        independent = (
            previous is None
            or ts_ns - previous >= self.artifacts.evidence_policy.decision_independence_ns
        )
        if independent:
            self._last_independent_ts_ns = ts_ns
        return independent

    def _resolve_markouts(self, mark: Decimal, now: int) -> tuple[PaperMarkout, ...]:
        resolved: list[PaperMarkout] = []
        for fill_id, fill in tuple(self._pending_markouts.items()):
            if now - fill.fill_ts_ns < self._markout_horizon_ns:
                continue
            direction = Decimal("1") if fill.side is OrderSide.BUY else Decimal("-1")
            value = direction * (mark - fill.price) / fill.price * Decimal("10000")
            resolved.append(
                PaperMarkout(
                    fill_id=fill_id,
                    horizon_ns=self._markout_horizon_ns,
                    observed_ts_ns=now,
                    mark_price=mark,
                    signed_markout_bps=value,
                )
            )
            del self._pending_markouts[fill_id]
        return tuple(resolved)

    def _settle_funding_if_due(self, mark: Decimal) -> None:
        if self._next_funding_ns is None or self._now_ns < self._next_funding_ns:
            return
        if self._now_ns - self._next_funding_ns >= FUNDING_INTERVAL_NS:
            self.journal.record_event(
                self.manifest.run_id,
                ts_ns=self._now_ns,
                kind="funding_gap",
                detail="missed one or more funding boundaries; no stale rate was backfilled",
            )
            self._next_funding_ns = (self._now_ns // FUNDING_INTERVAL_NS + 1) * FUNDING_INTERVAL_NS
            return
        self.simulator.settle_funding(
            funding_rate=self._funding_rate,
            mark_price=mark,
            settlement_ts_ns=self._next_funding_ns,
        )
        self._next_funding_ns += FUNDING_INTERVAL_NS

    def _record_economic_drills(self, reasons: tuple[RiskReason, ...], ts_ns: int) -> None:
        if self.simulator.open_orders:
            return
        mapping = {
            RiskReason.DAILY_LOSS_LIMIT: "daily_loss",
            RiskReason.DRAWDOWN_LIMIT: "drawdown",
        }
        for reason, drill in mapping.items():
            if reason in reasons:
                self.journal.record_drill(
                    self.manifest.run_id,
                    drill,
                    ts_ns=ts_ns,
                    evidence=f"risk authority enforced {reason.value} and initiated cancel-all",
                )


def manifest_identity(manifest: PaperRunManifest) -> str:
    return manifest.sha256()
