"""Bounded shadow metrics rendered to a read-only observer handoff file."""

from __future__ import annotations

from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.paper.engine import PaperEngineCycle, PaperTradingEngine


class ShadowMetrics:
    def __init__(self, registry: CollectorRegistry, output_path: Path) -> None:
        self.registry = registry
        self.output_path = output_path
        self.market_states = Counter(
            "aqt_shadow_market_states_total",
            "Causal L2 states processed by the isolated shadow engine",
            registry=registry,
        )
        self.decisions = Counter(
            "aqt_shadow_risk_decisions_total",
            "Shadow intents evaluated by production risk",
            ("result",),
            registry=registry,
        )
        self.commands = Counter(
            "aqt_shadow_commands_total",
            "Commands captured by the counterfactual-only sink",
            ("kind",),
            registry=registry,
        )
        self.ingress_latency = Histogram(
            "aqt_shadow_ingress_latency_seconds",
            "Gateway receive to isolated-engine completion latency",
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
            registry=registry,
        )
        self.cycle_latency = Histogram(
            "aqt_shadow_cycle_latency_seconds",
            "Feature, strategy, risk, simulation, and journal cycle latency",
            buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.05),
            registry=registry,
        )
        self.feed_connected = Gauge(
            "aqt_shadow_feed_connected", "Whether isolated ingress is current", registry=registry
        )
        self.feature_ready = Gauge(
            "aqt_shadow_feature_ready", "Whether feature warmup is complete", registry=registry
        )
        self.equity = Gauge(
            "aqt_shadow_counterfactual_equity_usd",
            "Marked counterfactual account equity",
            registry=registry,
        )
        self.position = Gauge(
            "aqt_shadow_counterfactual_position_base",
            "Counterfactual BTC position",
            registry=registry,
        )
        self.open_orders = Gauge(
            "aqt_shadow_counterfactual_open_orders",
            "Counterfactual open orders",
            registry=registry,
        )
        self.last_ingress_sequence = Gauge(
            "aqt_shadow_last_ingress_sequence",
            "Last durably processed gateway ingress sequence",
            registry=registry,
        )
        self.network_egress_capability = Gauge(
            "aqt_shadow_network_egress_capability",
            "Always zero after the no-default-route runtime proof",
            registry=registry,
        )
        self.network_egress_capability.set(0)

    def observe_cycle(
        self,
        engine: PaperTradingEngine,
        cycle: PaperEngineCycle,
        *,
        ingress_sequence: int,
        ingress_latency_seconds: float,
        cycle_latency_seconds: float,
    ) -> None:
        self.market_states.inc()
        self.ingress_latency.observe(ingress_latency_seconds)
        self.cycle_latency.observe(cycle_latency_seconds)
        for decision in cycle.decisions:
            self.decisions.labels(
                result="approved" if decision.risk_decision.allowed else "denied"
            ).inc()
        for command in cycle.commands:
            self.commands.labels(kind=command.kind.value).inc()
        self.update_state(engine, ingress_sequence=ingress_sequence)

    def update_state(self, engine: PaperTradingEngine, *, ingress_sequence: int) -> None:
        self.feed_connected.set(1 if engine.feed_connected else 0)
        self.feature_ready.set(1 if engine.feature_ready else 0)
        self.equity.set(float(engine.simulator.account.equity_usd))
        self.position.set(float(engine.simulator.account.position_base))
        self.open_orders.set(len(engine.simulator.open_orders))
        self.last_ingress_sequence.set(ingress_sequence)

    def publish(self) -> None:
        atomic_replace_bytes(self.output_path, generate_latest(self.registry))
