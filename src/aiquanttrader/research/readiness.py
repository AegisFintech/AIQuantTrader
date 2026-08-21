"""Fail-closed monitoring for continuous, normalized research-data retention."""

from __future__ import annotations

import shutil
import time
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic_core import to_jsonable_python

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.domain.base import CanonicalValue, canonical_sha256
from aiquanttrader.domain.data import (
    NormalizedSegmentManifest,
    QualityIssueKind,
    RawSegmentManifest,
    SegmentFinalizationReason,
)
from aiquanttrader.market_data.admission import DatasetQualityError, build_dataset_manifest
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.research.metrics import DataReadinessMetrics
from aiquanttrader.research.readiness_models import (
    ResearchDataReadinessGate,
    ResearchDataReadinessPolicy,
    ResearchDataReadinessReport,
    ResearchDataReadinessState,
)

_DAY_NS = 86_400_000_000_000


@dataclass(frozen=True, slots=True)
class _SegmentPair:
    raw: RawSegmentManifest
    normalized: NormalizedSegmentManifest


@dataclass(frozen=True, slots=True)
class _Discovery:
    raw_manifest_count: int
    normalized_manifest_count: int
    pairs: tuple[_SegmentPair, ...]
    invalid_manifest_count: int
    invalid_binding_count: int
    unpaired_raw_segment_count: int
    orphan_normalized_segment_count: int
    missing_normalized_file_count: int


def _load_raw_manifest(path: Path) -> RawSegmentManifest:
    return RawSegmentManifest.model_validate_json(path.read_bytes())


def _load_normalized_manifest(path: Path) -> NormalizedSegmentManifest:
    return NormalizedSegmentManifest.model_validate_json(path.read_bytes())


def required_validation_span_ns(policy: ValidationPolicy) -> int:
    """Return the minimum range that can produce the configured folds and holdout."""

    return (
        policy.train_ns
        + policy.purge_ns
        + policy.validation_ns
        + policy.embargo_ns
        + policy.test_ns
        + (policy.minimum_folds - 1) * policy.step_ns
        + policy.final_holdout_ns
    )


def load_data_readiness_policy(path: Path) -> ResearchDataReadinessPolicy:
    try:
        with path.resolve(strict=True).open("rb") as handle:
            return ResearchDataReadinessPolicy.model_validate(tomllib.load(handle))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid research-data readiness policy {path}: {exc}") from exc


def _load_validation_policy(path: Path) -> ValidationPolicy:
    try:
        with path.resolve(strict=True).open("rb") as handle:
            return ValidationPolicy.model_validate(tomllib.load(handle))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid validation policy {path}: {exc}") from exc


def load_readiness_inputs(
    readiness_policy_path: Path, validation_policy_path: Path
) -> tuple[ResearchDataReadinessPolicy, ValidationPolicy]:
    return (
        load_data_readiness_policy(readiness_policy_path),
        _load_validation_policy(validation_policy_path),
    )


def _discover_segments(root: Path, *, now_ns: int) -> _Discovery:
    raw_paths = sorted((root / "raw").rglob("*.manifest.json"))
    normalized_paths = sorted((root / "normalized" / "manifests").glob("*.json"))
    invalid_manifests = 0
    invalid_bindings = 0

    raw_by_id: dict[str, RawSegmentManifest] = {}
    duplicate_raw_ids: set[str] = set()
    for path in raw_paths:
        try:
            raw_manifest = _load_raw_manifest(path)
        except (OSError, ValueError):
            invalid_manifests += 1
            continue
        if raw_manifest.segment_id in raw_by_id:
            duplicate_raw_ids.add(raw_manifest.segment_id)
            invalid_bindings += 1
            continue
        raw_by_id[raw_manifest.segment_id] = raw_manifest

    normalized_by_id: dict[str, NormalizedSegmentManifest] = {}
    duplicate_normalized_ids: set[str] = set()
    for path in normalized_paths:
        try:
            normalized_manifest = _load_normalized_manifest(path)
        except (OSError, ValueError):
            invalid_manifests += 1
            continue
        if normalized_manifest.source_segment_id in normalized_by_id:
            duplicate_normalized_ids.add(normalized_manifest.source_segment_id)
            invalid_bindings += 1
            continue
        normalized_by_id[normalized_manifest.source_segment_id] = normalized_manifest

    ambiguous = duplicate_raw_ids | duplicate_normalized_ids
    raw_ids = set(raw_by_id)
    normalized_ids = set(normalized_by_id)
    pairs: list[_SegmentPair] = []
    missing_files = 0
    for segment_id in sorted(raw_ids & normalized_ids):
        if segment_id in ambiguous:
            continue
        raw = raw_by_id[segment_id]
        normalized = normalized_by_id[segment_id]
        if (
            raw.network != "mainnet"
            or raw.ended_at_ns > now_ns
            or normalized.source_segment_sha256 != raw.compressed_sha256
        ):
            invalid_bindings += 1
            continue
        complete = True
        for file in normalized.files:
            path = (root / file.relative_path).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.stat().st_size != file.byte_count
            ):
                missing_files += 1
                complete = False
        if complete:
            pairs.append(_SegmentPair(raw, normalized))

    pairs.sort(key=lambda item: (item.raw.started_at_ns, item.raw.segment_id))
    return _Discovery(
        raw_manifest_count=len(raw_paths),
        normalized_manifest_count=len(normalized_paths),
        pairs=tuple(pairs),
        invalid_manifest_count=invalid_manifests,
        invalid_binding_count=invalid_bindings,
        unpaired_raw_segment_count=len(raw_ids - normalized_ids),
        orphan_normalized_segment_count=len(normalized_ids - raw_ids),
        missing_normalized_file_count=missing_files,
    )


def _segment_quality_eligible(pair: _SegmentPair, policy: ResearchDataReadinessPolicy) -> bool:
    counts = Counter(issue.kind for issue in pair.normalized.issues)
    quality = policy.data_quality_policy
    limits = {
        QualityIssueKind.SCHEMA_ERROR: quality.max_schema_errors,
        QualityIssueKind.CROSSED_BOOK: quality.max_crossed_books,
        QualityIssueKind.TIMESTAMP_REGRESSION: quality.max_timestamp_regressions,
        QualityIssueKind.DUPLICATE: quality.max_duplicates,
    }
    return pair.normalized.excluded_frame_count <= policy.maximum_excluded_frames and all(
        counts[kind] <= limit for kind, limit in limits.items()
    )


def _contiguous_chains(
    pairs: tuple[_SegmentPair, ...], policy: ResearchDataReadinessPolicy
) -> tuple[tuple[tuple[_SegmentPair, ...], ...], int, int]:
    chains: list[tuple[_SegmentPair, ...]] = []
    current: list[_SegmentPair] = []
    breaks = 0
    overlaps = 0
    previous: _SegmentPair | None = None
    for pair in pairs:
        eligible = _segment_quality_eligible(pair, policy)
        must_break = not eligible
        if previous is not None:
            gap_ns = pair.raw.started_at_ns - previous.raw.ended_at_ns
            overlap = gap_ns < 0
            unexplained = (
                gap_ns > 0
                and previous.raw.finalization_reason is SegmentFinalizationReason.ERROR
                and policy.data_quality_policy.reject_unexplained_gaps
            )
            overlaps += int(overlap)
            must_break = (
                must_break or overlap or gap_ns > policy.maximum_contiguous_gap_ns or unexplained
            )
        if must_break:
            if current:
                chains.append(tuple(current))
                current.clear()
            breaks += 1
        if eligible:
            current.append(pair)
        previous = pair
    if current:
        chains.append(tuple(current))
    return tuple(chains), breaks, overlaps


def _chain_span(chain: tuple[_SegmentPair, ...]) -> int:
    if not chain:
        return 0
    return chain[-1].raw.ended_at_ns - chain[0].raw.started_at_ns


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _gate(name: str, passed: bool, actual: object, required: object) -> ResearchDataReadinessGate:
    return ResearchDataReadinessGate(
        gate=name,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )


def evaluate_data_readiness(
    *,
    data_root: Path,
    policy: ResearchDataReadinessPolicy,
    validation_policy: ValidationPolicy,
    generated_ts_ns: int | None = None,
) -> ResearchDataReadinessReport:
    """Evaluate metadata and capacity without reading labels or fitting a model."""

    now_ns = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    if now_ns < 0:
        raise ValueError("readiness generation timestamp cannot be negative")
    root = data_root.resolve(strict=True)
    discovery = _discover_segments(root, now_ns=now_ns)
    chains, continuity_breaks, overlaps = _contiguous_chains(discovery.pairs, policy)
    latest = chains[-1] if chains else ()
    latest_span = _chain_span(latest)
    longest_span = max((_chain_span(chain) for chain in chains), default=0)
    required_span = required_validation_span_ns(validation_policy)
    remaining_span = max(0, required_span - latest_span)
    completion_bps = min(10_000, latest_span * 10_000 // required_span)
    latest_start = latest[0].raw.started_at_ns if latest else None
    latest_end = latest[-1].raw.ended_at_ns if latest else None
    latest_age = now_ns - latest_end if latest_end is not None else None

    latest_issue_counts = Counter(
        issue.kind.value for pair in latest for issue in pair.normalized.issues
    )
    excluded_frames = sum(pair.normalized.excluded_frame_count for pair in latest)
    dataset_admitted = False
    if latest:
        try:
            build_dataset_manifest(
                ((pair.raw, pair.normalized) for pair in latest),
                policy.data_quality_policy,
                created_at=datetime.fromtimestamp(now_ns / 1e9, tz=UTC),
            )
            dataset_admitted = True
        except DatasetQualityError:
            dataset_admitted = False

    data_bytes = _directory_bytes(root)
    disk_free = shutil.disk_usage(root).free
    observed_segment_ns = sum(
        max(0, pair.raw.ended_at_ns - pair.raw.started_at_ns) for pair in discovery.pairs
    )
    storage_rate = (
        0
        if observed_segment_ns == 0
        else (data_bytes * _DAY_NS + observed_segment_ns - 1) // observed_segment_ns
    )
    projected_bytes = (
        storage_rate * remaining_span * policy.storage_projection_safety_bps + _DAY_NS * 10_000 - 1
    ) // (_DAY_NS * 10_000)
    storage_headroom = disk_free - policy.minimum_free_bytes

    artifact_errors = (
        discovery.invalid_manifest_count
        + discovery.invalid_binding_count
        + discovery.missing_normalized_file_count
    )
    gates = (
        _gate("artifact_metadata", artifact_errors == 0, artifact_errors, 0),
        _gate(
            "normalization_complete",
            discovery.unpaired_raw_segment_count == 0,
            discovery.unpaired_raw_segment_count,
            0,
        ),
        _gate(
            "normalized_lineage",
            discovery.orphan_normalized_segment_count == 0,
            discovery.orphan_normalized_segment_count,
            0,
        ),
        _gate("latest_chain_present", bool(latest), len(latest), ">= 1"),
        _gate("latest_dataset_admitted", dataset_admitted, dataset_admitted, True),
        _gate(
            "latest_excluded_frames",
            excluded_frames <= policy.maximum_excluded_frames,
            excluded_frames,
            f"<= {policy.maximum_excluded_frames}",
        ),
        _gate("latest_capture_span", latest_span >= required_span, latest_span, required_span),
        _gate(
            "latest_capture_freshness",
            latest_age is not None and 0 <= latest_age <= policy.maximum_latest_segment_age_ns,
            latest_age if latest_age is not None else "missing",
            f"in [0,{policy.maximum_latest_segment_age_ns}]",
        ),
        _gate(
            "disk_free_floor",
            disk_free >= policy.minimum_free_bytes,
            disk_free,
            f">= {policy.minimum_free_bytes}",
        ),
        _gate(
            "projected_storage_capacity",
            storage_rate > 0 and storage_headroom >= projected_bytes,
            f"headroom={storage_headroom},projected={projected_bytes},rate_per_day={storage_rate}",
            "positive observed rate and headroom >= safety-adjusted projected bytes",
        ),
    )
    payload = {
        "schema_version": 1,
        "generated_ts_ns": now_ns,
        "policy": policy,
        "validation_policy": validation_policy,
        "required_validation_span_ns": required_span,
        "raw_manifest_count": discovery.raw_manifest_count,
        "normalized_manifest_count": discovery.normalized_manifest_count,
        "paired_segment_count": len(discovery.pairs),
        "invalid_manifest_count": discovery.invalid_manifest_count,
        "invalid_binding_count": discovery.invalid_binding_count,
        "unpaired_raw_segment_count": discovery.unpaired_raw_segment_count,
        "orphan_normalized_segment_count": discovery.orphan_normalized_segment_count,
        "missing_normalized_file_count": discovery.missing_normalized_file_count,
        "overlap_count": overlaps,
        "continuity_break_count": continuity_breaks,
        "contiguous_chain_count": len(chains),
        "latest_contiguous_started_ts_ns": latest_start,
        "latest_contiguous_ended_ts_ns": latest_end,
        "latest_contiguous_span_ns": latest_span,
        "longest_contiguous_span_ns": longest_span,
        "remaining_validation_span_ns": remaining_span,
        "completion_bps": completion_bps,
        "latest_segment_age_ns": latest_age,
        "latest_chain_segment_count": len(latest),
        "latest_chain_raw_records": sum(pair.raw.record_count for pair in latest),
        "latest_chain_normalized_events": sum(pair.normalized.event_count for pair in latest),
        "latest_chain_excluded_frames": excluded_frames,
        "latest_chain_quality_issues": tuple(
            {"name": name, "count": count} for name, count in sorted(latest_issue_counts.items())
        ),
        "latest_chain_dataset_admitted": dataset_admitted,
        "data_bytes": data_bytes,
        "disk_free_bytes": disk_free,
        "storage_rate_bytes_per_day": storage_rate,
        "estimated_additional_bytes_required": projected_bytes,
        "storage_headroom_bytes": storage_headroom,
        "gates": gates,
        "model_training_authorized": False,
        "production_promotion_authorized": False,
    }
    ready = all(gate.passed for gate in gates)
    identity = cast(CanonicalValue, to_jsonable_python(payload))
    return ResearchDataReadinessReport.model_validate(
        {
            **payload,
            "report_id": canonical_sha256(identity),
            "ready_for_horizon_audit": ready,
        }
    )


class DataReadinessMonitor:
    """Single-process polling owner for readiness state and metrics."""

    def __init__(
        self,
        *,
        data_root: Path,
        state_root: Path,
        policy: ResearchDataReadinessPolicy,
        validation_policy: ValidationPolicy,
        metrics: DataReadinessMetrics,
    ) -> None:
        self.data_root = data_root
        self.state_path = state_root.resolve() / "research" / "data-readiness.json"
        self.policy = policy
        self.validation_policy = validation_policy
        self.metrics = metrics

    def write_state(
        self,
        status: str,
        *,
        report: ResearchDataReadinessReport | None = None,
        error: str | None = None,
        heartbeat_ts_ns: int | None = None,
    ) -> ResearchDataReadinessState:
        state = ResearchDataReadinessState.model_validate(
            {
                "status": status,
                "heartbeat_ts_ns": (time.time_ns() if heartbeat_ts_ns is None else heartbeat_ts_ns),
                "report": report,
                "last_error_code": error,
            }
        )
        atomic_replace_bytes(self.state_path, state.canonical_bytes() + b"\n")
        return state

    def run_once(self, *, generated_ts_ns: int | None = None) -> ResearchDataReadinessReport:
        report = evaluate_data_readiness(
            data_root=self.data_root,
            policy=self.policy,
            validation_policy=self.validation_policy,
            generated_ts_ns=generated_ts_ns,
        )
        self.metrics.observe(report, service_healthy=True)
        self.write_state("running", report=report, heartbeat_ts_ns=report.generated_ts_ns)
        return report

    def record_failure(self, exc: BaseException) -> None:
        self.metrics.set_service_healthy(False)
        self.write_state("failed", error=type(exc).__name__)
