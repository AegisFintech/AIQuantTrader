"""Bounded-cardinality Prometheus metrics for research automation."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class ResearchMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = CollectorRegistry() if registry is None else registry
        self.experiments = Counter(
            "aqt_research_experiments_total",
            "Research experiments by bounded stage and result",
            ("stage", "result"),
            registry=self.registry,
        )
        self.training_seconds = Histogram(
            "aqt_research_training_seconds",
            "Model training duration",
            ("engine", "target"),
            registry=self.registry,
        )
        self.promotion_gate = Gauge(
            "aqt_research_promotion_gate_passed",
            "Latest promotion gate outcome",
            ("strategy", "gate"),
            registry=self.registry,
        )
        self.maximum_drift_psi = Gauge(
            "aqt_research_feature_drift_psi_max",
            "Maximum latest feature population stability index",
            ("feature_set",),
            registry=self.registry,
        )
        self.current_stage = Gauge(
            "aqt_research_stage_info",
            "Current experiment count by stage",
            ("stage",),
            registry=self.registry,
        )
