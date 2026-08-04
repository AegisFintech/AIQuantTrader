"""Bounded-cardinality Prometheus metrics for the public recorder."""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge


@dataclass(frozen=True, slots=True)
class RecorderMetrics:
    registry: CollectorRegistry
    frames: Counter
    bytes: Counter
    reconnects: Counter
    issues: Counter
    last_frame_seconds: Gauge
    free_bytes: Gauge
    connected: Gauge
    segments: Counter

    @classmethod
    def create(cls, registry: CollectorRegistry | None = None) -> RecorderMetrics:
        target = registry or CollectorRegistry()
        return cls(
            registry=target,
            frames=Counter(
                "aqt_market_data_frames_total",
                "Inbound WebSocket frames archived",
                ("transport",),
                registry=target,
            ),
            bytes=Counter(
                "aqt_market_data_bytes_total",
                "Inbound WebSocket payload bytes archived",
                registry=target,
            ),
            reconnects=Counter(
                "aqt_market_data_reconnects_total",
                "WebSocket reconnect attempts",
                ("reason",),
                registry=target,
            ),
            issues=Counter(
                "aqt_market_data_quality_issues_total",
                "Observed quality issues",
                ("kind", "code"),
                registry=target,
            ),
            last_frame_seconds=Gauge(
                "aqt_market_data_last_frame_timestamp_seconds",
                "Wall-clock timestamp of the last inbound frame",
                registry=target,
            ),
            free_bytes=Gauge(
                "aqt_market_data_disk_free_bytes",
                "Free bytes on the recorder data filesystem",
                registry=target,
            ),
            connected=Gauge(
                "aqt_market_data_connected",
                "Whether the public WebSocket is connected",
                registry=target,
            ),
            segments=Counter(
                "aqt_market_data_segments_finalized_total",
                "Raw segments finalized",
                ("reason",),
                registry=target,
            ),
        )
