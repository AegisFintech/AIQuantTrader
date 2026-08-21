"""Dependency-light normalized dataset admission shared by workers and monitors."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.domain.data import (
    DataQualityPolicy,
    DatasetGap,
    DatasetManifest,
    GapClassification,
    NormalizedSegmentManifest,
    QualityIssueKind,
    RawSegmentManifest,
    SegmentFinalizationReason,
)


class DatasetQualityError(ValueError):
    pass


def _classify_gap(previous: RawSegmentManifest) -> GapClassification:
    return {
        SegmentFinalizationReason.ROTATION: GapClassification.PLANNED_ROTATION,
        SegmentFinalizationReason.DISCONNECT: GapClassification.VENUE_DISCONNECT,
        SegmentFinalizationReason.SHUTDOWN: GapClassification.RECORDER_RESTART,
        SegmentFinalizationReason.STALE_FEED: GapClassification.STALE_FEED_RECOVERY,
        SegmentFinalizationReason.DISK_PRESSURE: GapClassification.DISK_PRESSURE,
        SegmentFinalizationReason.ERROR: GapClassification.UNEXPLAINED,
    }[previous.finalization_reason]


def build_dataset_manifest(
    segments: Iterable[tuple[RawSegmentManifest, NormalizedSegmentManifest]],
    policy: DataQualityPolicy,
    *,
    created_at: datetime | None = None,
) -> DatasetManifest:
    ordered = sorted(segments, key=lambda item: (item[0].started_at_ns, item[0].segment_id))
    if not ordered:
        raise DatasetQualityError("dataset requires at least one segment")
    issue_counts: Counter[QualityIssueKind] = Counter(
        issue.kind for _, normalized in ordered for issue in normalized.issues
    )
    limits = {
        QualityIssueKind.SCHEMA_ERROR: policy.max_schema_errors,
        QualityIssueKind.CROSSED_BOOK: policy.max_crossed_books,
        QualityIssueKind.TIMESTAMP_REGRESSION: policy.max_timestamp_regressions,
        QualityIssueKind.DUPLICATE: policy.max_duplicates,
    }
    for kind, limit in limits.items():
        if issue_counts[kind] > limit:
            raise DatasetQualityError(f"{kind.value} count {issue_counts[kind]} exceeds {limit}")

    gaps: list[DatasetGap] = []
    for (previous, _), (current, _) in pairwise(ordered):
        duration = max(0, current.started_at_ns - previous.ended_at_ns)
        if duration == 0:
            continue
        classification = _classify_gap(previous)
        gap = DatasetGap(
            start_ts_ns=previous.ended_at_ns,
            end_ts_ns=current.started_at_ns,
            duration_ns=duration,
            classification=classification,
            previous_segment_id=previous.segment_id,
            next_segment_id=current.segment_id,
        )
        gaps.append(gap)
        if classification is GapClassification.UNEXPLAINED and policy.reject_unexplained_gaps:
            raise DatasetQualityError("dataset contains an unexplained recorder gap")
        if duration > policy.max_classified_gap_ns:
            raise DatasetQualityError(
                f"classified gap {duration}ns exceeds {policy.max_classified_gap_ns}ns"
            )

    normalized_hashes = tuple(normalized.sha256() for _, normalized in ordered)
    policy_hash = policy.sha256()
    identity_payload: Mapping[str, Any] = {
        "normalized_manifest_sha256s": normalized_hashes,
        "policy_sha256": policy_hash,
        "gaps": [gap.model_dump(mode="json") for gap in gaps],
        "market_wide_liquidations_available": False,
    }
    return DatasetManifest(
        dataset_id=canonical_sha256(identity_payload),
        normalized_manifest_sha256s=normalized_hashes,
        policy_sha256=policy_hash,
        gaps=tuple(gaps),
        created_at=datetime.now(UTC) if created_at is None else created_at,
    )
