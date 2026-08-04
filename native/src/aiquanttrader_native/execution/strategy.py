"""The sole Nautilus strategy permitted to issue normal exchange commands."""

from __future__ import annotations

import time
import uuid
from collections import deque
from decimal import Decimal
from typing import Any, Literal, Protocol

from nautilus_trader.model.enums import OrderSide as NautilusOrderSide
from nautilus_trader.model.enums import TimeInForce as NautilusTimeInForce
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

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

    def on_start(self) -> None:
        self._subscribe_quotes()
        self._heartbeat.set_health(execution_healthy=False, reconciliation_complete=False)
        self._metrics.set_operational_state(
            reconciled=False,
            unresolved_commands=self._journal.unresolved_command_count(),
        )

    def on_stop(self) -> None:
        self._heartbeat.set_health(execution_healthy=False, reconciliation_complete=False)
        self._metrics.set_operational_state(
            reconciled=False,
            unresolved_commands=self._journal.unresolved_command_count(),
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

        adapter_started = time.perf_counter()
        try:
            self._cancel_all_nautilus()
        except Exception:
            self._metrics.observe_adapter(
                "cancel_all", "error", time.perf_counter() - adapter_started
            )
            raise
        self._metrics.observe_adapter(
            "cancel_all", "success", time.perf_counter() - adapter_started
        )

    def update_health(self, snapshot: RiskSnapshot, decision: RiskDecision) -> None:
        """Refresh the sentinel lease only after the authority validates freshness."""

        healthy = (
            snapshot.exchange_connected
            and snapshot.reconciliation_complete
            and snapshot.deployment_approved
            and not snapshot.operator_kill
            and decision.state in {RiskState.ACTIVE, RiskState.REDUCE_ONLY, RiskState.FLATTENING}
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
        self._record_order_event(event, ExecutionState.REJECTED, self._event_reason(event))

    def on_order_denied(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.DENIED, self._event_reason(event))

    def on_order_canceled(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.CANCELED, "order canceled")

    def on_order_cancel_rejected(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.ACCEPTED, self._event_reason(event))

    def on_order_modify_rejected(self, event: Any) -> None:
        self._record_order_event(event, ExecutionState.ACCEPTED, self._event_reason(event))

    def on_order_filled(self, event: Any) -> None:
        order = self._cached_order(event.client_order_id)
        final = bool(order is not None and order.is_closed)
        state = ExecutionState.FILLED if final else ExecutionState.PARTIALLY_FILLED
        filled = (
            Decimal(str(order.filled_qty)) if order is not None else Decimal(str(event.last_qty))
        )
        self._record_order_event(event, state, "fill received", filled_quantity=filled)

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
    ) -> None:
        client_order_id = str(event.client_order_id)
        row = self._journal.by_client_order_id(client_order_id)
        if row is None:
            return
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
