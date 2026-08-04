"""Bounded-cardinality execution and risk metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from aiquanttrader_native.domain.execution import ExecutionState, RiskDecision, RiskState


class ExecutionMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = CollectorRegistry() if registry is None else registry
        self.risk_decisions = Counter(
            "aqt_execution_risk_decisions_total",
            "Synchronous order risk decisions",
            ["result", "state", "reason"],
            registry=self.registry,
        )
        self.order_events = Counter(
            "aqt_execution_order_events_total",
            "Durably journaled order lifecycle events",
            ["state"],
            registry=self.registry,
        )
        self.risk_state = Gauge(
            "aqt_execution_risk_state",
            "Current risk state as a one-hot gauge",
            ["state"],
            registry=self.registry,
        )
        self.risk_decision_latency_seconds = Histogram(
            "aqt_execution_risk_decision_latency_seconds",
            "Wall time for synchronous risk evaluation",
            buckets=(0.00001, 0.000025, 0.00005, 0.0001, 0.00025, 0.0005, 0.001, 0.005),
            registry=self.registry,
        )
        self.journal_commit_latency_seconds = Histogram(
            "aqt_execution_journal_commit_latency_seconds",
            "Wall time for durable execution-journal transactions",
            ["operation"],
            buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.05),
            registry=self.registry,
        )
        self.adapter_command_latency_seconds = Histogram(
            "aqt_execution_adapter_command_latency_seconds",
            "Wall time to hand a command to the Nautilus execution engine",
            ["operation", "result"],
            buckets=(0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.1),
            registry=self.registry,
        )
        self.reconciliation_complete = Gauge(
            "aqt_execution_reconciliation_complete",
            "Whether execution reconciliation is complete",
            registry=self.registry,
        )
        self.unresolved_commands = Gauge(
            "aqt_execution_unresolved_commands",
            "Commands awaiting an authoritative exchange outcome",
            registry=self.registry,
        )
        self.deployment_admission_active = Gauge(
            "aqt_execution_deployment_admission_active",
            "Whether the exact signed deployment remains durably admitted",
            registry=self.registry,
        )
        self.approval_expiry_seconds = Gauge(
            "aqt_execution_approval_expiry_seconds",
            "Signed deployment approval expiry as Unix seconds",
            registry=self.registry,
        )
        self.approved_capital_limit_usd = Gauge(
            "aqt_execution_approved_capital_limit_usd",
            "Maximum account or vault equity authorized by signed approval",
            registry=self.registry,
        )

    def observe_decision(self, decision: RiskDecision, *, latency_seconds: float) -> None:
        self.risk_decision_latency_seconds.observe(latency_seconds)
        result = "approved" if decision.allowed else "denied"
        for state in RiskState:
            self.risk_state.labels(state=state.value).set(1 if state is decision.state else 0)
        for reason in decision.reasons:
            self.risk_decisions.labels(
                result=result,
                state=decision.state.value,
                reason=reason.value,
            ).inc()

    def observe_event(self, state: ExecutionState) -> None:
        self.order_events.labels(state=state.value).inc()

    def observe_journal(self, operation: str, latency_seconds: float) -> None:
        self.journal_commit_latency_seconds.labels(operation=operation).observe(latency_seconds)

    def observe_adapter(self, operation: str, result: str, latency_seconds: float) -> None:
        self.adapter_command_latency_seconds.labels(operation=operation, result=result).observe(
            latency_seconds
        )

    def set_operational_state(self, *, reconciled: bool, unresolved_commands: int) -> None:
        self.reconciliation_complete.set(1 if reconciled else 0)
        self.unresolved_commands.set(unresolved_commands)

    def set_deployment_admission(
        self,
        *,
        active: bool,
        expiry_seconds: float | None = None,
        capital_limit_usd: float | None = None,
    ) -> None:
        self.deployment_admission_active.set(1 if active else 0)
        if expiry_seconds is not None:
            self.approval_expiry_seconds.set(expiry_seconds)
        if capital_limit_usd is not None:
            self.approved_capital_limit_usd.set(capital_limit_usd)
