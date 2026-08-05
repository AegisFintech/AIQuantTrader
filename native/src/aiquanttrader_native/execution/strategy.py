"""The sole Nautilus strategy permitted to issue normal exchange commands."""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal, Protocol

from nautilus_trader.model.enums import OrderSide as NautilusOrderSide
from nautilus_trader.model.enums import TimeInForce as NautilusTimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

from aiquanttrader_native.acceptance.audit import OperationalEvidenceLog
from aiquanttrader_native.acceptance.models import OperationalEventKind
from aiquanttrader_native.config.models import RiskLimits
from aiquanttrader_native.domain.execution import (
    ExecutionJournalEvent,
    ExecutionState,
    OrderIntent,
    OrderKind,
    RiskDecision,
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
    build_live_risk_snapshot,
    read_live_account_state,
)
from aiquanttrader_native.execution.metrics import ExecutionMetrics
from aiquanttrader_native.risk.authority import ApprovalError, RiskAuthority


class AdmissionGuard(Protocol):
    @property
    def capital_limit_usd(self) -> Decimal: ...

    def require_active(self) -> object: ...

    def is_active(self) -> bool: ...


class RiskManagedExecutionStrategy(Strategy):  # type: ignore[misc]
    """Translate approved intents; future alpha kernels never inherit from Strategy."""

    def __init__(
        self,
        *,
        authority: RiskAuthority,
        journal: ExecutionJournal,
        limits: RiskLimits,
        heartbeat: HeartbeatPublisher,
        metrics: ExecutionMetrics | None = None,
        admission_guard: AdmissionGuard | None = None,
        live_pipeline: LiveDecisionPipeline | None = None,
        equity_baselines: EquityBaselineStore | None = None,
        connectivity_probe: Callable[[], bool] | None = None,
        estimated_taker_fee_bps: Decimal = Decimal("4.5"),
        estimated_slippage_bps: Decimal = Decimal("1"),
        operational_log: OperationalEvidenceLog | None = None,
    ) -> None:
        super().__init__(
            StrategyConfig(
                strategy_id="RISK-GATEWAY-001",
                use_uuid_client_order_ids=True,
                log_events=True,
                log_commands=True,
            )
        )
        self._authority = authority
        self._journal = journal
        self._limits = limits
        self._heartbeat = heartbeat
        self._metrics = ExecutionMetrics() if metrics is None else metrics
        self._admission_guard = admission_guard
        self._cancel_times_ns: deque[int] = deque()
        live_dependencies = (live_pipeline, equity_baselines, connectivity_probe)
        if any(item is not None for item in live_dependencies) and not all(
            item is not None for item in live_dependencies
        ):
            raise ValueError("live strategy dependencies must be supplied together")
        self._live_pipeline = live_pipeline
        self._equity_baselines = equity_baselines
        self._connectivity_probe = connectivity_probe
        self._estimated_taker_fee_bps = estimated_taker_fee_bps
        self._estimated_slippage_bps = estimated_slippage_bps
        self._operational_log = operational_log
        self._funding_rate = Decimal("0")
        self._mark_price: tuple[Decimal, int] | None = None
        self._startup_order_drain = False
        self._risk_cancel_pending = False
        self._pending_strategy_cancels: set[str] = set()
        self._reconciliation_complete = False
        self._last_risk_state: RiskState | None = None

    def on_start(self) -> None:
        if self._live_pipeline is None:
            self._subscribe_quotes()
        else:
            self._subscribe_live_data()
            # Nautilus invokes strategies only after execution reconciliation and
            # portfolio initialization complete successfully.
            self._reconciliation_complete = True
            startup_orders = self._open_orders()
            self._startup_order_drain = bool(startup_orders)
            if self._startup_order_drain:
                self.cancel_all()
                self._risk_cancel_pending = True
            self._record_operational(
                kind=OperationalEventKind.RECONCILIATION,
                success=True,
                detail="Nautilus startup reconciliation and portfolio initialization completed",
                order_count=len(startup_orders),
            )
        self._heartbeat.set_health(
            execution_healthy=False,
            reconciliation_complete=self._reconciliation_complete,
        )
        self._metrics.set_operational_state(
            reconciled=self._reconciliation_complete,
            unresolved_commands=self._journal.unresolved_command_count(),
        )

    def on_stop(self) -> None:
        self._reconciliation_complete = False
        self._heartbeat.set_health(execution_healthy=False, reconciliation_complete=False)
        self._metrics.set_operational_state(
            reconciled=False,
            unresolved_commands=self._journal.unresolved_command_count(),
        )
        self._record_operational(
            kind=OperationalEventKind.RECONCILIATION,
            success=True,
            detail="trading strategy stopped and reconciliation readiness was cleared",
        )

    def execute_intent(
        self, intent: OrderIntent, snapshot: RiskSnapshot
    ) -> tuple[RiskDecision, str | None]:
        """Risk-check and submit one idempotent order from the Nautilus event loop."""

        risk_started = time.perf_counter()
        guarded_snapshot = self._guarded_snapshot(snapshot)
        decision = self._authority.evaluate(intent, guarded_snapshot)
        self.update_health(guarded_snapshot, decision)
        self._metrics.observe_decision(
            decision,
            latency_seconds=time.perf_counter() - risk_started,
        )
        if not decision.allowed:
            self._journal_begin(
                self._event(
                    intent.intent_id,
                    ExecutionState.PENDING_SUBMIT,
                    "intent entered synchronous risk gate",
                    source="risk",
                )
            )
            self._journal_append(
                self._event(
                    intent.intent_id,
                    ExecutionState.DENIED,
                    ",".join(reason.value for reason in decision.reasons),
                    source="risk",
                )
            )
            return decision, None

        order = self._make_order(intent)
        client_order_id = str(order.client_order_id)
        self._journal_begin(
            self._event(
                intent.intent_id,
                ExecutionState.PENDING_SUBMIT,
                "intent and client order identity durably reserved",
                source="risk",
                client_order_id=client_order_id,
            )
        )
        try:
            if self._admission_guard is not None:
                self._admission_guard.require_active()
            self._authority.consume(decision, intent, guarded_snapshot)
        except (ApprovalError, ValueError) as exc:
            self._journal_append(
                self._event(
                    intent.intent_id,
                    ExecutionState.DENIED,
                    f"approval invalid before dispatch: {type(exc).__name__}",
                    source="risk",
                    client_order_id=client_order_id,
                )
            )
            raise
        self._journal_append(
            self._event(
                intent.intent_id,
                ExecutionState.SUBMITTED,
                "authorized adapter dispatch beginning",
                source="nautilus",
                client_order_id=client_order_id,
            )
        )
        adapter_started = time.perf_counter()
        try:
            self._submit_nautilus(order)
        except Exception as exc:
            self._metrics.observe_adapter("submit", "error", time.perf_counter() - adapter_started)
            self._journal_append(
                self._event(
                    intent.intent_id,
                    ExecutionState.UNKNOWN,
                    f"submission raised {type(exc).__name__}; reconcile before any retry",
                    source="nautilus",
                    client_order_id=client_order_id,
                )
            )
            raise
        self._metrics.observe_adapter("submit", "success", time.perf_counter() - adapter_started)
        return decision, client_order_id

    def replace_order(
        self,
        *,
        intent_id: str,
        replacement: OrderIntent,
        snapshot: RiskSnapshot,
    ) -> RiskDecision:
        """Risk-check a Hyperliquid cancel-replace while preserving the original CLOID."""

        row = self._require_order(intent_id)
        client_order_id = ClientOrderId(row["client_order_id"])
        order = self._cached_order(client_order_id)
        if order is None:
            raise ValueError("cannot modify an order absent from the reconciled Nautilus cache")
        if replacement.kind is not OrderKind.LIMIT or replacement.limit_price is None:
            raise ValueError("cancel-replace requires a priced limit intent")
        risk_started = time.perf_counter()
        guarded_snapshot = self._guarded_snapshot(snapshot)
        decision = self._authority.evaluate(replacement, guarded_snapshot)
        self.update_health(guarded_snapshot, decision)
        self._metrics.observe_decision(
            decision,
            latency_seconds=time.perf_counter() - risk_started,
        )
        if not decision.allowed:
            self._journal_append(
                self._event(
                    intent_id,
                    ExecutionState(row["state"]),
                    "replacement denied: " + ",".join(reason.value for reason in decision.reasons),
                    source="risk",
                    client_order_id=str(client_order_id),
                )
            )
            return decision
        if self._admission_guard is not None:
            self._admission_guard.require_active()
        self._authority.consume(decision, replacement, guarded_snapshot)
        self._journal_append(
            self._event(
                intent_id,
                ExecutionState.PENDING_MODIFY,
                f"approved replacement intent {replacement.intent_id}",
                source="risk",
                client_order_id=str(client_order_id),
            )
        )
        adapter_started = time.perf_counter()
        try:
            self._modify_nautilus(
                order,
                quantity=Quantity.from_str(str(replacement.quantity_base)),
                price=Price.from_str(str(replacement.limit_price)),
            )
        except Exception as exc:
            self._metrics.observe_adapter("modify", "error", time.perf_counter() - adapter_started)
            self._journal_append(
                self._event(
                    intent_id,
                    ExecutionState.UNKNOWN,
                    f"modify raised {type(exc).__name__}; reconcile original CLOID",
                    source="nautilus",
                    client_order_id=str(client_order_id),
                )
            )
            raise
        self._metrics.observe_adapter("modify", "success", time.perf_counter() - adapter_started)
        return decision

    def cancel(self, intent_id: str) -> None:
        """Cancel an order without requiring exposure approval; cancellation is always available."""

        self._consume_cancel_budget()
        row = self._require_order(intent_id)
        client_order_id = ClientOrderId(row["client_order_id"])
        order = self._cached_order(client_order_id)
        if order is None:
            raise ValueError("cannot cancel an order absent from the reconciled Nautilus cache")
        self._journal_append(
            self._event(
                intent_id,
                ExecutionState.PENDING_CANCEL,
                "cancel handed to Nautilus execution engine",
                source="operator",
                client_order_id=str(client_order_id),
            )
        )
        adapter_started = time.perf_counter()
        try:
            self._cancel_nautilus(order)
        except Exception as exc:
            self._metrics.observe_adapter("cancel", "error", time.perf_counter() - adapter_started)
            self._journal_append(
                self._event(
                    intent_id,
                    ExecutionState.UNKNOWN,
                    f"cancel raised {type(exc).__name__}; reconcile original CLOID",
                    source="nautilus",
                    client_order_id=str(client_order_id),
                )
            )
            raise
        self._metrics.observe_adapter("cancel", "success", time.perf_counter() - adapter_started)

    def cancel_all(self) -> None:
        """Cancel all BTC orders through Nautilus; the sentinel remains the crash fallback."""

        self._consume_cancel_budget()
        try:
            order_count = len(self._open_orders())
        except Exception:
            order_count = None
        adapter_started = time.perf_counter()
        try:
            self._cancel_all_nautilus()
        except Exception as exc:
            self._metrics.observe_adapter(
                "cancel_all", "error", time.perf_counter() - adapter_started
            )
            self._record_operational(
                kind=OperationalEventKind.EXECUTION_CANCEL_ALL,
                success=False,
                detail=f"Nautilus cancel-all raised {type(exc).__name__}",
                order_count=order_count,
            )
            raise
        self._metrics.observe_adapter(
            "cancel_all", "success", time.perf_counter() - adapter_started
        )
        self._record_operational(
            kind=OperationalEventKind.EXECUTION_CANCEL_ALL,
            success=True,
            detail="Nautilus accepted the BTC cancel-all command",
            order_count=order_count,
        )

    def update_health(self, snapshot: RiskSnapshot, decision: RiskDecision) -> None:
        """Refresh the sentinel lease only after the authority validates freshness."""

        self.update_risk_health(snapshot, decision.state)

    def update_risk_health(self, snapshot: RiskSnapshot, state: RiskState) -> None:
        """Refresh liveness from a state evaluation even when alpha emits no order."""

        healthy = (
            snapshot.exchange_connected
            and snapshot.reconciliation_complete
            and snapshot.deployment_approved
            and not snapshot.operator_kill
            and state in {RiskState.ACTIVE, RiskState.REDUCE_ONLY, RiskState.FLATTENING}
        )
        self._heartbeat.set_health(
            execution_healthy=healthy,
            reconciliation_complete=snapshot.reconciliation_complete,
            valid_for_ms=min(
                self._limits.public_data_stale_after_ms,
                self._limits.private_data_stale_after_ms,
            ),
        )
        self._metrics.set_operational_state(
            reconciled=snapshot.reconciliation_complete,
            unresolved_commands=self._journal.unresolved_command_count(),
        )

    def _guarded_snapshot(self, snapshot: RiskSnapshot) -> RiskSnapshot:
        if self._admission_guard is None:
            return snapshot
        active = self._admission_guard.is_active()
        self._metrics.set_deployment_admission(
            active=active,
            capital_limit_usd=float(self._admission_guard.capital_limit_usd),
        )
        return snapshot.model_copy(
            update={
                "deployment_approved": active,
                "approved_capital_limit_usd": self._admission_guard.capital_limit_usd,
            }
        )

    def on_order_submitted(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.SUBMITTED, "venue submission dispatched")

    def on_order_accepted(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.ACCEPTED, "order accepted")

    def on_order_updated(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.ACCEPTED, "cancel-replace accepted")

    def on_order_rejected(self, event: Any) -> None:
        intent_id = self._record_order_event(
            event, ExecutionState.REJECTED, self._event_reason(event)
        )
        self._release_terminal_intent(intent_id)

    def on_order_denied(self, event: Any) -> None:
        intent_id = self._record_order_event(
            event, ExecutionState.DENIED, self._event_reason(event)
        )
        self._release_terminal_intent(intent_id)

    def on_order_canceled(self, event: Any) -> None:
        intent_id = self._record_order_event(event, ExecutionState.CANCELED, "order canceled")
        self._release_terminal_intent(intent_id)
        self._risk_cancel_pending = False

    def on_order_expired(self, event: Any) -> None:
        intent_id = self._record_order_event(event, ExecutionState.CANCELED, "order expired")
        self._release_terminal_intent(intent_id)

    def on_order_cancel_rejected(self, event: Any) -> None:
        intent_id = self._record_order_event(
            event, ExecutionState.ACCEPTED, self._event_reason(event)
        )
        if intent_id is not None:
            self._pending_strategy_cancels.discard(intent_id)
        self._risk_cancel_pending = False

    def on_order_modify_rejected(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.ACCEPTED, self._event_reason(event))

    def on_order_filled(self, event: Any) -> None:
        order = self._cached_order(event.client_order_id)
        final = bool(order is not None and order.is_closed)
        state = ExecutionState.FILLED if final else ExecutionState.PARTIALLY_FILLED
        filled = (
            Decimal(str(order.filled_qty)) if order is not None else Decimal(str(event.last_qty))
        )
        intent_id = self._record_order_event(event, state, "fill received", filled_quantity=filled)
        if final:
            self._release_terminal_intent(intent_id)

    def on_trade_tick(self, tick: Any) -> None:
        if self._live_pipeline is None:
            return
        try:
            self._live_pipeline.market.observe_trade(tick)
        except Exception:
            self._fail_closed_live_cycle()
            raise

    def on_mark_price(self, update: Any) -> None:
        if str(update.instrument_id) == "BTC-USD-PERP.HYPERLIQUID":
            self._mark_price = (Decimal(str(update.value)), int(update.ts_init))

    def on_funding_rate(self, update: Any) -> None:
        if str(update.instrument_id) == "BTC-USD-PERP.HYPERLIQUID":
            self._funding_rate = Decimal(str(update.rate))

    def on_order_book_deltas(self, deltas: Any) -> None:
        if self._live_pipeline is None:
            return
        try:
            book = self.cache.order_book(deltas.instrument_id)
            if book is None:
                raise ValueError("managed Nautilus order book is unavailable")
            market = self._live_pipeline.market.observe_book(book, deltas)
            self._process_live_market(market)
        except Exception:
            self._fail_closed_live_cycle()
            raise

    def _make_order(self, intent: OrderIntent) -> Any:
        factory = self._get_order_factory()
        if factory is None:
            raise RuntimeError("execution strategy is not registered with a TradingNode")
        side = NautilusOrderSide.BUY if intent.side is OrderSide.BUY else NautilusOrderSide.SELL
        tif = (
            NautilusTimeInForce.GTC
            if intent.time_in_force is TimeInForce.GTC
            else NautilusTimeInForce.IOC
        )
        instrument_id = InstrumentId.from_str(intent.instrument_id)
        quantity = Quantity.from_str(str(intent.quantity_base))
        if intent.kind is OrderKind.MARKET:
            return factory.market(
                instrument_id=instrument_id,
                order_side=side,
                quantity=quantity,
                time_in_force=tif,
                reduce_only=intent.reduce_only,
                tags=[f"intent:{intent.intent_id}"],
            )
        if intent.limit_price is None:
            raise ValueError("limit intent is missing its price")
        return factory.limit(
            instrument_id=instrument_id,
            order_side=side,
            quantity=quantity,
            price=Price.from_str(str(intent.limit_price)),
            time_in_force=tif,
            post_only=intent.post_only,
            reduce_only=intent.reduce_only,
            tags=[f"intent:{intent.intent_id}"],
        )

    def _subscribe_quotes(self) -> None:
        self.subscribe_quote_ticks(InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID"))

    def _subscribe_live_data(self) -> None:
        if self._live_pipeline is None:
            raise RuntimeError("live subscriptions require a decision pipeline")
        instrument_id = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
        self.subscribe_order_book_deltas(
            instrument_id,
            depth=self._live_pipeline.artifacts.feature_config.depth_levels,
            managed=True,
        )
        self.subscribe_trade_ticks(instrument_id)
        self.subscribe_mark_prices(instrument_id)
        self.subscribe_funding_rates(instrument_id)

    def _process_live_market(self, market: Any) -> None:
        pipeline = self._live_pipeline
        if pipeline is None:
            return
        snapshot = self._guarded_snapshot(self._live_risk_snapshot(market))
        state, reasons = self._authority.state(snapshot)
        self.update_risk_health(snapshot, state)
        if state is not self._last_risk_state:
            self._record_operational(
                kind=OperationalEventKind.RISK_STATE,
                success=True,
                detail=",".join(reason.value for reason in reasons) or "active",
                risk_state=state,
            )
            self._last_risk_state = state
        if state is not RiskState.ACTIVE:
            if snapshot.open_order_count and not self._risk_cancel_pending:
                self.cancel_all()
                self._risk_cancel_pending = True
                self._metrics.observe_live_action("cancel_all", "dispatched")
            if snapshot.open_order_count or state in {RiskState.CANCEL_ONLY, RiskState.HALTED}:
                self._metrics.observe_live_cycle(result="risk_blocked")
                return
        if self._risk_cancel_pending:
            if snapshot.open_order_count:
                self._metrics.observe_live_cycle(result="risk_recovery_drain")
                return
            self._risk_cancel_pending = False
        if self._startup_order_drain:
            if snapshot.open_order_count:
                if not self._risk_cancel_pending:
                    self.cancel_all()
                    self._risk_cancel_pending = True
                self._metrics.observe_live_cycle(result="startup_drain")
                return
            self._startup_order_drain = False

        margin_utilization = min(
            Decimal("1"),
            snapshot.leverage / self._limits.max_leverage,
        )
        cycle = pipeline.decide(
            market,
            position_base=snapshot.position_base,
            margin_utilization=margin_utilization,
            funding_rate=self._funding_rate,
            estimated_taker_fee_bps=self._estimated_taker_fee_bps,
            estimated_slippage_bps=self._estimated_slippage_bps,
        )
        dispatched_cancels: set[str] = set()
        dispatched_intents: set[str] = set()
        cancellations = cycle.transition.decision.cancel_intent_ids
        if cancellations:
            for intent_id in cancellations:
                if intent_id in self._pending_strategy_cancels:
                    continue
                row = self._journal.current(intent_id)
                if row is None:
                    raise ValueError(f"live strategy references an unjournaled intent: {intent_id}")
                if ExecutionState(row["state"]) in {
                    ExecutionState.FILLED,
                    ExecutionState.CANCELED,
                    ExecutionState.REJECTED,
                    ExecutionState.DENIED,
                }:
                    pipeline.release_intent(intent_id)
                    continue
                self.cancel(intent_id)
                self._pending_strategy_cancels.add(intent_id)
                dispatched_cancels.add(intent_id)
                self._metrics.observe_live_action("cancel", "dispatched")
            pipeline.commit(
                cycle,
                dispatched_intent_ids=dispatched_intents,
                dispatched_cancel_ids=dispatched_cancels,
            )
            self._metrics.observe_live_cycle(
                result="cancel_before_replace",
                feature_ready=cycle.features.ready,
            )
            return

        try:
            for intent in cycle.transition.decision.submit:
                current_snapshot = self._live_risk_snapshot(market)
                decision, client_order_id = self.execute_intent(intent, current_snapshot)
                result = "dispatched" if client_order_id is not None else "denied"
                self._metrics.observe_live_action("submit", result)
                if decision.allowed and client_order_id is not None:
                    dispatched_intents.add(intent.intent_id)
        finally:
            pipeline.commit(
                cycle,
                dispatched_intent_ids=dispatched_intents,
                dispatched_cancel_ids=dispatched_cancels,
            )
        self._metrics.observe_live_cycle(result="processed", feature_ready=cycle.features.ready)

    def _live_risk_snapshot(self, market: Any) -> RiskSnapshot:
        if self._equity_baselines is None or self._connectivity_probe is None:
            raise RuntimeError("live risk state dependencies are unavailable")
        now_ns = time.time_ns()
        account = self._live_account_state()
        baseline = self._equity_baselines.observe(account.equity_usd, now_ns=now_ns)
        mid = (market.bids[0].price + market.asks[0].price) / Decimal("2")
        mark = mid
        if self._mark_price is not None:
            value, received_ns = self._mark_price
            if received_ns <= now_ns and (
                now_ns - received_ns <= self._limits.public_data_stale_after_ms * 1_000_000
            ):
                mark = value
        connected = self._connectivity_probe()
        reconciled = self._reconciliation_complete and self._journal.unknown_command_count() == 0
        self._metrics.set_live_account(
            equity_usd=float(account.equity_usd),
            position_base=float(account.position_base),
        )
        return build_live_risk_snapshot(
            now_ns=now_ns,
            public_data_ts_ns=market.observed_ts_ns,
            mark_price=mark,
            account=account,
            baseline=baseline,
            exchange_connected=connected,
            reconciliation_complete=reconciled,
        )

    def _live_account_state(self) -> LiveAccountState:
        return read_live_account_state(self.portfolio, self.cache)

    def _open_orders(self) -> list[Any]:
        return list(
            self.cache.orders_open(instrument_id=InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID"))
        )

    def _release_terminal_intent(self, intent_id: str | None) -> None:
        if intent_id is None:
            return
        self._pending_strategy_cancels.discard(intent_id)
        if self._live_pipeline is not None:
            self._live_pipeline.release_intent(intent_id)

    def _fail_closed_live_cycle(self) -> None:
        self._metrics.observe_live_cycle(result="error")
        self._heartbeat.set_health(execution_healthy=False, reconciliation_complete=False)
        try:
            if self._open_orders():
                self.cancel_all()
        except Exception:
            pass
        self._record_operational(
            kind=OperationalEventKind.LIVE_PIPELINE_FAULT,
            success=False,
            detail="live market normalization or decision processing failed closed",
        )

    def _record_operational(
        self,
        *,
        kind: OperationalEventKind,
        success: bool,
        detail: str,
        order_count: int | None = None,
        risk_state: RiskState | None = None,
    ) -> None:
        if self._operational_log is None:
            return
        self._operational_log.append(
            kind=kind,
            event_ts_ns=time.time_ns(),
            success=success,
            detail=detail,
            order_count=order_count,
            risk_state=risk_state,
        )

    def _submit_nautilus(self, order: Any) -> None:
        super().submit_order(order)

    def _modify_nautilus(self, order: Any, *, quantity: Quantity, price: Price) -> None:
        super().modify_order(order, quantity=quantity, price=price)

    def _cancel_nautilus(self, order: Any) -> None:
        super().cancel_order(order)

    def _cancel_all_nautilus(self) -> None:
        super().cancel_all_orders(InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID"))

    def _cached_order(self, client_order_id: ClientOrderId) -> Any:
        return self.cache.order(client_order_id)

    def _get_order_factory(self) -> Any:
        return self.order_factory

    def _record_order_event(
        self,
        event: Any,
        state: ExecutionState,
        detail: str,
        *,
        filled_quantity: Decimal = Decimal("0"),
    ) -> str | None:
        client_order_id = str(event.client_order_id)
        row = self._journal.by_client_order_id(client_order_id)
        if row is None:
            return None
        venue_order_id = getattr(event, "venue_order_id", None)
        self._journal_append(
            self._event(
                row["intent_id"],
                state,
                detail,
                source="nautilus",
                client_order_id=client_order_id,
                venue_order_id=None if venue_order_id is None else str(venue_order_id),
                filled_quantity=filled_quantity,
            )
        )
        self._metrics.observe_event(state)
        return str(row["intent_id"])

    def _journal_begin(self, event: ExecutionJournalEvent) -> None:
        started = time.perf_counter()
        try:
            self._journal.begin(event)
        finally:
            self._metrics.observe_journal("begin", time.perf_counter() - started)
            self._metrics.unresolved_commands.set(self._journal.unresolved_command_count())

    def _journal_append(self, event: ExecutionJournalEvent) -> None:
        started = time.perf_counter()
        try:
            self._journal.append(event)
        finally:
            self._metrics.observe_journal("append", time.perf_counter() - started)
            self._metrics.unresolved_commands.set(self._journal.unresolved_command_count())

    def _consume_cancel_budget(self) -> None:
        now = time.time_ns()
        cutoff = now - 1_000_000_000
        while self._cancel_times_ns and self._cancel_times_ns[0] <= cutoff:
            self._cancel_times_ns.popleft()
        if len(self._cancel_times_ns) >= self._limits.max_cancels_per_second:
            raise ValueError("cancel rate limit reached")
        self._cancel_times_ns.append(now)

    def _require_order(self, intent_id: str) -> Any:
        row = self._journal.current(intent_id)
        if row is None or row["client_order_id"] is None:
            raise ValueError(f"no submitted order for intent {intent_id}")
        if ExecutionState(row["state"]) in {
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.DENIED,
        }:
            raise ValueError(f"intent {intent_id} is already terminal")
        return row

    @staticmethod
    def _event_reason(event: Any) -> str:
        reason = getattr(event, "reason", None)
        return str(reason)[:1_024] if reason else type(event).__name__

    @staticmethod
    def _event(
        intent_id: str,
        state: ExecutionState,
        detail: str,
        *,
        source: Literal["risk", "nautilus", "reconciliation", "operator"],
        client_order_id: str | None = None,
        venue_order_id: str | None = None,
        filled_quantity: Decimal = Decimal("0"),
    ) -> ExecutionJournalEvent:
        return ExecutionJournalEvent(
            event_id=str(uuid.uuid4()),
            intent_id=intent_id,
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            state=state,
            event_ts_ns=time.time_ns(),
            filled_quantity_base=filled_quantity,
            detail=detail,
            source=source,
        )
