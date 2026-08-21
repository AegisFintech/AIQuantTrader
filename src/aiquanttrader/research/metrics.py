"""Bounded-cardinality Prometheus metrics for research automation."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from aiquanttrader.research.readiness_models import ResearchDataReadinessReport


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


class DataReadinessMetrics:
    """Dedicated low-cardinality metrics for the always-on readiness monitor."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = CollectorRegistry() if registry is None else registry
        self.service_healthy = Gauge(
            "aqt_research_readiness_service_healthy",
            "Whether the readiness monitor completed its latest evaluation",
            registry=self.registry,
        )
        self.ready = Gauge(
            "aqt_research_data_ready",
            "Whether retained data passes every gate for a sealed horizon audit",
            registry=self.registry,
        )
        self.capture_span_seconds = Gauge(
            "aqt_research_data_capture_span_seconds",
            "Retained contiguous capture spans",
            ("scope",),
            registry=self.registry,
        )
        self.completion_ratio = Gauge(
            "aqt_research_data_completion_ratio",
            "Latest contiguous span divided by the required validation span",
            registry=self.registry,
        )
        self.segment_count = Gauge(
            "aqt_research_data_segments",
            "Segment and manifest counts by bounded state",
            ("state",),
            registry=self.registry,
        )
        self.artifact_error_count = Gauge(
            "aqt_research_data_artifact_errors",
            "Artifact errors by bounded category",
            ("kind",),
            registry=self.registry,
        )
        self.latest_segment_age_seconds = Gauge(
            "aqt_research_data_latest_segment_age_seconds",
            "Age of the latest segment in the latest admissible chain",
            registry=self.registry,
        )
        self.disk_bytes = Gauge(
            "aqt_research_data_disk_bytes",
            "Research-data storage bytes by bounded state",
            ("state",),
            registry=self.registry,
        )
        self.storage_rate_bytes_per_day = Gauge(
            "aqt_research_data_storage_rate_bytes_per_day",
            "Observed retained-data growth rate",
            registry=self.registry,
        )
        self.gate = Gauge(
            "aqt_research_data_gate_passed",
            "Latest research-data readiness gate result",
            ("gate",),
            registry=self.registry,
        )

    def set_service_healthy(self, healthy: bool) -> None:
        self.service_healthy.set(int(healthy))

    def observe(self, report: ResearchDataReadinessReport, *, service_healthy: bool) -> None:
        self.set_service_healthy(service_healthy)
        self.ready.set(int(report.ready_for_horizon_audit))
        self.capture_span_seconds.labels(scope="latest").set(
            report.latest_contiguous_span_ns / 1_000_000_000
        )
        self.capture_span_seconds.labels(scope="longest").set(
            report.longest_contiguous_span_ns / 1_000_000_000
        )
        self.capture_span_seconds.labels(scope="required").set(
            report.required_validation_span_ns / 1_000_000_000
        )
        self.completion_ratio.set(report.completion_bps / 10_000)
        for state, value in (
            ("raw_manifests", report.raw_manifest_count),
            ("normalized_manifests", report.normalized_manifest_count),
            ("paired", report.paired_segment_count),
            ("latest_chain", report.latest_chain_segment_count),
            ("chains", report.contiguous_chain_count),
        ):
            self.segment_count.labels(state=state).set(value)
        for kind, value in (
            ("invalid_manifest", report.invalid_manifest_count),
            ("invalid_binding", report.invalid_binding_count),
            ("unpaired_raw", report.unpaired_raw_segment_count),
            ("orphan_normalized", report.orphan_normalized_segment_count),
            ("missing_normalized_file", report.missing_normalized_file_count),
            ("overlap", report.overlap_count),
            ("continuity_break", report.continuity_break_count),
        ):
            self.artifact_error_count.labels(kind=kind).set(value)
        self.latest_segment_age_seconds.set(
            0
            if report.latest_segment_age_ns is None
            else report.latest_segment_age_ns / 1_000_000_000
        )
        for state, value in (
            ("used_by_data", report.data_bytes),
            ("free", report.disk_free_bytes),
            ("reserve", report.policy.minimum_free_bytes),
            ("headroom", max(0, report.storage_headroom_bytes)),
            ("projected_required", report.estimated_additional_bytes_required),
        ):
            self.disk_bytes.labels(state=state).set(value)
        self.storage_rate_bytes_per_day.set(report.storage_rate_bytes_per_day)
        for gate in report.gates:
            self.gate.labels(gate=gate.gate).set(int(gate.passed))
