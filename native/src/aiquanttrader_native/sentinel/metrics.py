"""Bounded-cardinality safety-sentinel metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge


class SentinelMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = CollectorRegistry() if registry is None else registry
        self.heartbeat_age_seconds = Gauge(
            "aqt_sentinel_trading_heartbeat_age_seconds",
            "Age of the trading-node heartbeat",
            registry=self.registry,
        )
        self.trading_node_healthy = Gauge(
            "aqt_sentinel_trading_node_healthy",
            "Whether the heartbeat is current, reconciled, and execution healthy",
            registry=self.registry,
        )
        self.deadman_deadline_seconds = Gauge(
            "aqt_sentinel_deadman_deadline_seconds",
            "Current scheduled exchange cancellation deadline as Unix seconds",
            registry=self.registry,
        )
        self.deadman_renewals = Counter(
            "aqt_sentinel_deadman_renewals_total",
            "Successful dead-man renewals",
            registry=self.registry,
        )
        self.emergency_cancels = Counter(
            "aqt_sentinel_emergency_cancel_total",
            "Emergency cancel-all attempts",
            ["result"],
            registry=self.registry,
        )
        self.errors = Counter(
            "aqt_sentinel_errors_total",
            "Sentinel operation errors",
            ["operation"],
            registry=self.registry,
        )
        self.deployment_admission_active = Gauge(
            "aqt_sentinel_deployment_admission_active",
            "Whether the signed deployment admission is active",
            registry=self.registry,
        )
