"""Fail-closed, content-addressed evidence for deployment-host market-data soaks."""

from __future__ import annotations

import math
import re
import tomllib
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import cast

from prometheus_client.parser import text_string_to_metric_families
from pydantic_core import to_jsonable_python

from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.domain.base import CanonicalValue, canonical_sha256
from aiquanttrader.domain.data import (
    MarketDataNamedCount,
    MarketDataQualityCount,
    MarketDataRecorderMetricsSnapshot,
    MarketDataSoakGateResult,
    MarketDataSoakPolicy,
    MarketDataSoakReport,
    NormalizedSegmentManifest,
    NormalizerState,
    QualityIssueKind,
    RawSegmentManifest,
    RecorderState,
    SegmentFinalizationReason,
)
from aiquanttrader.market_data.raw import RawSegmentReader, load_segment_manifest
from aiquanttrader.market_data.storage import (
    DatasetQualityError,
    build_dataset_manifest,
    load_normalized_manifest,
    validate_normalized_files,
)

_ARTIFACT_STARTED_NS = re.compile(r"-(?P<started>[0-9]{16,20})-")


def load_soak_policy(path: Path) -> MarketDataSoakPolicy:
    try:
        with path.resolve(strict=True).open("rb") as handle:
            payload = tomllib.load(handle)
        return MarketDataSoakPolicy.model_validate(payload)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid market-data soak policy {path}: {exc}") from exc


def parse_recorder_metrics(
    payload: str,
    *,
    captured_ts_ns: int,
) -> MarketDataRecorderMetricsSnapshot:
    if captured_ts_ns < 0:
        raise ValueError("metrics capture timestamp cannot be negative")
    samples: dict[str, list[tuple[Mapping[str, str], float]]] = defaultdict(list)
    try:
        for family in text_string_to_metric_families(payload):
            for sample in family.samples:
                samples[sample.name].append((sample.labels, float(sample.value)))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid Prometheus metrics snapshot: {exc}") from exc

    frames = _sum_counter(samples, "aqt_market_data_frames_total")
    payload_bytes = _scalar_counter(samples, "aqt_market_data_bytes_total")
    reconnects = _labeled_counts(
        samples, "aqt_market_data_reconnects_total", label="reason"
    )
    finalized = _labeled_counts(
        samples, "aqt_market_data_segments_finalized_total", label="reason"
    )
    for item in finalized:
        SegmentFinalizationReason(item.name)

    quality: list[MarketDataQualityCount] = []
    for labels, value in samples.get("aqt_market_data_quality_issues_total", []):
        if set(labels) != {"kind", "code"}:
            raise ValueError("quality issue metric has unexpected labels")
        quality.append(
            MarketDataQualityCount(
                kind=QualityIssueKind(labels["kind"]),
                code=labels["code"],
                count=_counter_value(value, "aqt_market_data_quality_issues_total"),
            )
        )
    quality.sort(key=lambda item: (item.kind.value, item.code))

    last_frame_seconds = _scalar(samples, "aqt_market_data_last_frame_timestamp_seconds")
    disk_free = _scalar(samples, "aqt_market_data_disk_free_bytes")
    connected = _scalar(samples, "aqt_market_data_connected")
    if connected not in {0.0, 1.0}:
        raise ValueError("connected metric must be exactly zero or one")
    if last_frame_seconds < 0 or disk_free < 0 or not disk_free.is_integer():
        raise ValueError("recorder gauge values are outside their valid domains")
    return MarketDataRecorderMetricsSnapshot(
        captured_ts_ns=captured_ts_ns,
        frames=frames,
        payload_bytes=payload_bytes,
        reconnects=reconnects,
        quality_issues=tuple(quality),
        last_frame_ts_ns=round(last_frame_seconds * 1_000_000_000),
        disk_free_bytes=int(disk_free),
        connected=bool(connected),
        finalized_segments=finalized,
    )


def evaluate_market_data_soak(
    *,
    bundle: ConfigBundle,
    policy: MarketDataSoakPolicy,
    data_root: Path,
    state_root: Path,
    metrics: MarketDataRecorderMetricsSnapshot,
    requested_started_ts_ns: int,
    runtime_code_identity: str,
    collector_code_identity: str,
    image_digest: str,
    expected_config_fingerprint: str,
    start_free_bytes: int,
    recorder_restart_count: int,
    normalizer_restart_count: int,
) -> MarketDataSoakReport:
    if requested_started_ts_ns < 0 or requested_started_ts_ns > metrics.captured_ts_ns:
        raise ValueError("requested soak start must not follow metrics capture")
    if min(start_free_bytes, recorder_restart_count, normalizer_restart_count) < 0:
        raise ValueError("disk and restart observations cannot be negative")
    root = data_root.resolve(strict=True)
    state = state_root.resolve(strict=True)
    recorder_state = _load_recorder_state(state)
    normalizer_state = _load_normalizer_state(state)
    pairs = _discover_and_verify_segments(
        root,
        started_ts_ns=requested_started_ts_ns,
        ended_ts_ns=metrics.captured_ts_ns,
    )

    raw_manifests = tuple(raw for raw, _ in pairs)
    normalized_manifests = tuple(normalized for _, normalized in pairs)
    observation_started = (
        raw_manifests[0].started_at_ns if raw_manifests else requested_started_ts_ns
    )
    observation_ended = (
        raw_manifests[-1].ended_at_ns if raw_manifests else requested_started_ts_ns
    )
    observation_ns = observation_ended - observation_started
    overlap_count = sum(
        current.started_at_ns < previous.ended_at_ns
        for previous, current in pairwise(raw_manifests)
    )
    incomplete, corrupt = _count_failed_artifacts(
        root,
        started_ts_ns=requested_started_ts_ns,
        ended_ts_ns=metrics.captured_ts_ns,
        current_segment_id=recorder_state.current_segment_id,
    )

    dataset = None
    dataset_error: str | None = None
    try:
        dataset = build_dataset_manifest(
            pairs,
            policy.data_quality_policy,
            created_at=datetime.fromtimestamp(metrics.captured_ts_ns / 1e9, tz=UTC),
        )
    except DatasetQualityError:
        dataset_error = "quality_policy_rejected"

    raw_records = sum(item.record_count for item in raw_manifests)
    raw_payload_bytes = sum(item.payload_bytes for item in raw_manifests)
    normalized_events = sum(item.event_count for item in normalized_manifests)
    excluded_frames = sum(item.excluded_frame_count for item in normalized_manifests)
    finalization_counts = Counter(item.finalization_reason.value for item in raw_manifests)
    normalized_issue_counts = Counter(
        issue.kind.value for item in normalized_manifests for issue in item.issues
    )
    reconnects = sum(item.count for item in metrics.reconnects)
    metrics_finalized = sum(item.count for item in metrics.finalized_segments)
    recorder_age = metrics.captured_ts_ns - recorder_state.heartbeat_ts_ns
    normalizer_age = metrics.captured_ts_ns - normalizer_state.heartbeat_ts_ns
    last_frame_age = metrics.captured_ts_ns - metrics.last_frame_ts_ns
    configured_floor = bundle.settings.market_data.minimum_free_bytes
    disk_floor = max(configured_floor, policy.minimum_free_bytes)
    disallowed_reasons = sorted(
        reason
        for reason in finalization_counts
        if SegmentFinalizationReason(reason) not in policy.allowed_finalization_reasons
    )
    critical_metric_limits = {
        QualityIssueKind.SCHEMA_ERROR: policy.data_quality_policy.max_schema_errors,
        QualityIssueKind.CROSSED_BOOK: policy.data_quality_policy.max_crossed_books,
        QualityIssueKind.TIMESTAMP_REGRESSION: (
            policy.data_quality_policy.max_timestamp_regressions
        ),
        QualityIssueKind.DUPLICATE: policy.data_quality_policy.max_duplicates,
        QualityIssueKind.SILENCE: 0,
        QualityIssueKind.RECONNECT: 0,
        QualityIssueKind.DISK_PRESSURE: 0,
        QualityIssueKind.UNEXPLAINED_GAP: 0,
    }
    metric_issue_counts = Counter[QualityIssueKind]()
    for item in metrics.quality_issues:
        metric_issue_counts[item.kind] += item.count

    settings = bundle.settings
    gates: list[MarketDataSoakGateResult] = [
        _gate(
            "config_identity",
            bundle.fingerprint == expected_config_fingerprint,
            bundle.fingerprint,
            expected_config_fingerprint,
        ),
        _gate(
            "public_mainnet_only",
            settings.environment == "paper"
            and settings.exchange.network.value == "mainnet"
            and settings.market_data.enabled
            and not settings.execution.enabled
            and not settings.can_submit_orders,
            (
                f"environment={settings.environment},network={settings.exchange.network.value},"
                f"market_data={settings.market_data.enabled},execution={settings.execution.enabled},"
                f"can_submit={settings.can_submit_orders}"
            ),
            "environment=paper,network=mainnet,market_data=True,execution=False,can_submit=False",
        ),
        _gate(
            "start_alignment",
            observation_started - requested_started_ts_ns <= policy.maximum_start_lag_ns,
            observation_started - requested_started_ts_ns,
            f"<= {policy.maximum_start_lag_ns}",
        ),
        _gate(
            "observation_window",
            observation_ns >= policy.minimum_observation_ns,
            observation_ns,
            f">= {policy.minimum_observation_ns}",
        ),
        _gate(
            "finalized_segments",
            len(raw_manifests) >= policy.minimum_finalized_segments,
            len(raw_manifests),
            f">= {policy.minimum_finalized_segments}",
        ),
        _gate("segment_overlap", overlap_count == 0, overlap_count, "0"),
        _gate(
            "finalization_reasons",
            not disallowed_reasons,
            ",".join(disallowed_reasons) or "allowed",
            ",".join(reason.value for reason in policy.allowed_finalization_reasons),
        ),
        _gate(
            "excluded_frames",
            excluded_frames <= policy.maximum_excluded_frames,
            excluded_frames,
            f"<= {policy.maximum_excluded_frames}",
        ),
        _gate(
            "failed_artifacts",
            incomplete == 0 and corrupt == 0,
            f"incomplete={incomplete},corrupt={corrupt}",
            "incomplete=0,corrupt=0",
        ),
        _gate(
            "dataset_admission",
            dataset is not None,
            dataset.dataset_id if dataset is not None else "quality_policy_rejected",
            "admitted",
        ),
        _gate("recorder_connected", metrics.connected, metrics.connected, "True"),
        _gate(
            "recorder_reconnects",
            reconnects <= policy.maximum_reconnects
            and recorder_state.reconnect_count == reconnects,
            f"metrics={reconnects},state={recorder_state.reconnect_count}",
            f"matching and <= {policy.maximum_reconnects}",
        ),
        _gate(
            "recorder_state",
            recorder_state.status == "connected"
            and recorder_state.network == "mainnet"
            and recorder_state.environment == "paper"
            and recorder_state.last_error_code is None,
            (
                f"status={recorder_state.status},network={recorder_state.network},"
                f"environment={recorder_state.environment},error={recorder_state.last_error_code}"
            ),
            "status=connected,network=mainnet,environment=paper,error=None",
        ),
        _gate(
            "normalizer_state",
            normalizer_state.status == "running"
            and normalizer_state.quarantined == 0
            and normalizer_state.last_error_code is None,
            (
                f"status={normalizer_state.status},quarantined={normalizer_state.quarantined},"
                f"error={normalizer_state.last_error_code}"
            ),
            "status=running,quarantined=0,error=None",
        ),
        _gate(
            "recorder_freshness",
            0 <= recorder_age <= policy.recorder_state_stale_after_ns
            and 0 <= last_frame_age <= policy.recorder_state_stale_after_ns,
            f"state_age_ns={recorder_age},frame_age_ns={last_frame_age}",
            f"both in [0,{policy.recorder_state_stale_after_ns}]",
        ),
        _gate(
            "normalizer_freshness",
            0 <= normalizer_age <= policy.normalizer_state_stale_after_ns,
            normalizer_age,
            f"in [0,{policy.normalizer_state_stale_after_ns}]",
        ),
        _gate(
            "recorder_restarts",
            recorder_restart_count <= policy.maximum_recorder_restarts,
            recorder_restart_count,
            f"<= {policy.maximum_recorder_restarts}",
        ),
        _gate(
            "normalizer_restarts",
            normalizer_restart_count <= policy.maximum_normalizer_restarts,
            normalizer_restart_count,
            f"<= {policy.maximum_normalizer_restarts}",
        ),
        _gate(
            "counter_consistency",
            metrics.frames >= raw_records
            and metrics.payload_bytes >= raw_payload_bytes
            and metrics_finalized >= len(raw_manifests),
            (
                f"frames={metrics.frames}/{raw_records},bytes={metrics.payload_bytes}/"
                f"{raw_payload_bytes},segments={metrics_finalized}/{len(raw_manifests)}"
            ),
            "each metric >= its finalized-artifact count",
        ),
        _gate(
            "start_disk_floor",
            start_free_bytes >= disk_floor,
            start_free_bytes,
            f">= {disk_floor}",
        ),
        _gate(
            "end_disk_floor",
            metrics.disk_free_bytes >= disk_floor,
            metrics.disk_free_bytes,
            f">= {disk_floor}",
        ),
    ]
    for kind, limit in critical_metric_limits.items():
        gates.append(
            _gate(
                f"metric_{kind.value}",
                metric_issue_counts[kind] <= limit,
                metric_issue_counts[kind],
                f"<= {limit}",
            )
        )

    report_payload = {
        "schema_version": 1,
        "generated_ts_ns": metrics.captured_ts_ns,
        "requested_started_ts_ns": requested_started_ts_ns,
        "observation_started_ts_ns": observation_started,
        "observation_ended_ts_ns": observation_ended,
        "observation_ns": observation_ns,
        "runtime_code_identity": runtime_code_identity,
        "collector_code_identity": collector_code_identity,
        "image_digest": image_digest,
        "environment": settings.environment,
        "network": settings.exchange.network.value,
        "instrument_id": settings.instrument.instrument_id,
        "config_fingerprint": bundle.fingerprint,
        "execution_enabled": settings.execution.enabled,
        "can_submit_orders": settings.can_submit_orders,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "raw_manifest_sha256s": tuple(item.sha256() for item in raw_manifests),
        "dataset_manifest": dataset,
        "dataset_admission_error": dataset_error,
        "raw_segments": len(raw_manifests),
        "raw_records": raw_records,
        "raw_payload_bytes": raw_payload_bytes,
        "normalized_events": normalized_events,
        "excluded_frames": excluded_frames,
        "overlap_count": overlap_count,
        "incomplete_artifacts": incomplete,
        "corrupt_artifacts": corrupt,
        "finalization_reasons": tuple(
            MarketDataNamedCount(name=name, count=count)
            for name, count in sorted(finalization_counts.items())
        ),
        "normalized_quality_issues": tuple(
            MarketDataNamedCount(name=name, count=count)
            for name, count in sorted(normalized_issue_counts.items())
        ),
        "recorder_state": recorder_state,
        "normalizer_state": normalizer_state,
        "metrics": metrics,
        "recorder_restart_count": recorder_restart_count,
        "normalizer_restart_count": normalizer_restart_count,
        "start_free_bytes": start_free_bytes,
        "end_free_bytes": metrics.disk_free_bytes,
        "gates": tuple(gates),
    }
    accepted = all(gate.passed for gate in gates)
    identity_payload = cast(CanonicalValue, to_jsonable_python(report_payload))
    return MarketDataSoakReport.model_validate(
        {
            **report_payload,
            "report_id": canonical_sha256(identity_payload),
            "accepted": accepted,
        }
    )


def _discover_and_verify_segments(
    root: Path,
    *,
    started_ts_ns: int,
    ended_ts_ns: int,
) -> tuple[tuple[RawSegmentManifest, NormalizedSegmentManifest], ...]:
    selected: list[tuple[RawSegmentManifest, Path]] = []
    raw_root = root / "raw"
    for manifest_path in sorted(raw_root.rglob("*.manifest.json")):
        manifest = load_segment_manifest(manifest_path)
        if manifest.started_at_ns < started_ts_ns or manifest.ended_at_ns > ended_ts_ns:
            continue
        if manifest.network != "mainnet":
            raise ValueError("soak window contains a non-mainnet raw segment")
        selected.append((manifest, manifest_path))
    selected.sort(key=lambda item: (item[0].started_at_ns, item[0].segment_id))
    identities = [item.segment_id for item, _ in selected]
    if len(set(identities)) != len(identities):
        raise ValueError("soak window contains duplicate raw segment identities")

    pairs: list[tuple[RawSegmentManifest, NormalizedSegmentManifest]] = []
    for raw, manifest_path in selected:
        segment_path = root / raw.relative_path
        reader = RawSegmentReader(segment_path, manifest_path)
        reader.verify()
        if reader.manifest != raw:
            raise ValueError("verified raw manifest differs from discovered manifest")
        normalized_path = (
            root / "normalized" / "manifests" / f"{raw.segment_id}.normalized.manifest.json"
        )
        normalized = load_normalized_manifest(normalized_path)
        if normalized.source_segment_id != raw.segment_id:
            raise DatasetQualityError("normalized segment identity differs from raw source")
        if normalized.source_segment_sha256 != raw.compressed_sha256:
            raise DatasetQualityError("raw and normalized source digests differ")
        validate_normalized_files(normalized, root)
        pairs.append((raw, normalized))
    return tuple(pairs)


def _count_failed_artifacts(
    root: Path,
    *,
    started_ts_ns: int,
    ended_ts_ns: int,
    current_segment_id: str | None,
) -> tuple[int, int]:
    incomplete = 0
    for path in (root / "raw").rglob("*.partial"):
        if current_segment_id is not None and current_segment_id in path.name:
            continue
        if _artifact_in_window(path, started_ts_ns, ended_ts_ns):
            incomplete += 1
    for path in (root / "quarantine" / "raw-incomplete").rglob("*"):
        if path.is_file() and _artifact_in_window(path, started_ts_ns, ended_ts_ns):
            incomplete += 1
    corrupt = sum(
        path.is_file() and _artifact_in_window(path, started_ts_ns, ended_ts_ns)
        for path in (root / "quarantine" / "raw-corrupt").rglob("*")
    )
    return incomplete, corrupt


def _artifact_in_window(path: Path, started_ts_ns: int, ended_ts_ns: int) -> bool:
    match = _ARTIFACT_STARTED_NS.search(path.name)
    return bool(match and started_ts_ns <= int(match.group("started")) <= ended_ts_ns)


def _load_recorder_state(root: Path) -> RecorderState:
    path = root / "market-data" / "recorder-state.json"
    try:
        return RecorderState.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid recorder state {path}: {exc}") from exc


def _load_normalizer_state(root: Path) -> NormalizerState:
    path = root / "market-data" / "normalizer-state.json"
    try:
        return NormalizerState.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid normalizer state {path}: {exc}") from exc


def _gate(gate: str, passed: bool, actual: object, required: object) -> MarketDataSoakGateResult:
    return MarketDataSoakGateResult(
        gate=gate,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )


def _scalar(
    samples: Mapping[str, list[tuple[Mapping[str, str], float]]],
    name: str,
) -> float:
    values = samples.get(name, [])
    if len(values) != 1 or values[0][0]:
        raise ValueError(f"metric {name} must contain one unlabeled sample")
    value = values[0][1]
    if not math.isfinite(value):
        raise ValueError(f"metric {name} must be finite")
    return value


def _counter_value(value: float, name: str) -> int:
    if not math.isfinite(value) or value < 0 or not value.is_integer():
        raise ValueError(f"counter {name} must be a finite non-negative integer")
    return int(value)


def _scalar_counter(
    samples: Mapping[str, list[tuple[Mapping[str, str], float]]],
    name: str,
) -> int:
    return _counter_value(_scalar(samples, name), name)


def _sum_counter(
    samples: Mapping[str, list[tuple[Mapping[str, str], float]]],
    name: str,
) -> int:
    values = samples.get(name, [])
    if not values:
        raise ValueError(f"required metric is missing: {name}")
    return sum(_counter_value(value, name) for _, value in values)


def _labeled_counts(
    samples: Mapping[str, list[tuple[Mapping[str, str], float]]],
    name: str,
    *,
    label: str,
) -> tuple[MarketDataNamedCount, ...]:
    result: list[MarketDataNamedCount] = []
    for labels, value in samples.get(name, []):
        if set(labels) != {label}:
            raise ValueError(f"metric {name} has unexpected labels")
        result.append(
            MarketDataNamedCount(name=labels[label], count=_counter_value(value, name))
        )
    return tuple(sorted(result, key=lambda item: item.name))
