"""Deterministic raw-to-Parquet normalization and research dataset admission."""

from __future__ import annotations

import os
import secrets
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import zstandard
from pydantic import ValidationError

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.domain.data import (
    DataQualityPolicy,
    DatasetGap,
    DatasetManifest,
    GapClassification,
    NormalizedFileManifest,
    NormalizedSegmentManifest,
    QualityIssueKind,
    RawSegmentManifest,
    SegmentFinalizationReason,
)
from aiquanttrader_native.domain.market import MarketEvent
from aiquanttrader_native.market_data.integrity import IntegrityTracker
from aiquanttrader_native.market_data.io import atomic_write_bytes, fsync_directory, sha256_file
from aiquanttrader_native.market_data.protocol import ProtocolError, parse_frame
from aiquanttrader_native.market_data.raw import RawSegmentError, RawSegmentReader

NORMALIZER_VERSION = "normalizer-v1"


class QuarantinedSegmentError(RawSegmentError):
    def __init__(self, message: str, paths: tuple[Path, ...]) -> None:
        super().__init__(message)
        self.paths = paths


class DatasetQualityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    manifest_path: Path
    manifest: NormalizedSegmentManifest


COMMON_FIELDS = [
    pa.field("schema_version", pa.int16(), nullable=False),
    pa.field("event_order", pa.int64(), nullable=False),
    pa.field("event_id", pa.string(), nullable=False),
    pa.field("venue", pa.string(), nullable=False),
    pa.field("instrument_id", pa.string()),
    pa.field("event_ts_ns", pa.uint64(), nullable=False),
    pa.field("receive_ts_ns", pa.uint64(), nullable=False),
    pa.field("connection_id", pa.string(), nullable=False),
    pa.field("source", pa.string(), nullable=False),
    pa.field("event_ts_source", pa.string(), nullable=False),
    pa.field("source_record_id", pa.string()),
]

LEVEL = pa.struct(
    [
        pa.field("price", pa.string(), nullable=False),
        pa.field("size", pa.string(), nullable=False),
        pa.field("order_count", pa.int64()),
    ]
)

EVENT_FIELDS: dict[str, list[pa.Field]] = {
    "l2_book": [
        pa.field("bids", pa.list_(LEVEL), nullable=False),
        pa.field("asks", pa.list_(LEVEL), nullable=False),
        pa.field("is_snapshot", pa.bool_(), nullable=False),
    ],
    "bbo": [
        pa.field("bid_price", pa.string(), nullable=False),
        pa.field("bid_size", pa.string(), nullable=False),
        pa.field("ask_price", pa.string(), nullable=False),
        pa.field("ask_size", pa.string(), nullable=False),
    ],
    "trade": [
        pa.field("trade_id", pa.string(), nullable=False),
        pa.field("price", pa.string(), nullable=False),
        pa.field("size", pa.string(), nullable=False),
        pa.field("aggressor", pa.string(), nullable=False),
        pa.field("transaction_hash", pa.string()),
    ],
    "funding": [
        pa.field("funding_rate", pa.string(), nullable=False),
        pa.field("next_funding_ts_ns", pa.uint64()),
    ],
    "open_interest": [pa.field("open_interest_base", pa.string(), nullable=False)],
    "mark_price": [pa.field("mark_price", pa.string(), nullable=False)],
    "index_price": [pa.field("index_price", pa.string(), nullable=False)],
    "account_liquidation": [
        pa.field("account_address", pa.string()),
        pa.field("liquidation_id", pa.string(), nullable=False),
        pa.field("liquidator_address", pa.string(), nullable=False),
        pa.field("liquidated_user_address", pa.string(), nullable=False),
        pa.field("liquidated_notional_usd", pa.string(), nullable=False),
        pa.field("liquidated_account_value_usd", pa.string(), nullable=False),
    ],
    "liquidation_fill": [
        pa.field("side", pa.string(), nullable=False),
        pa.field("price", pa.string(), nullable=False),
        pa.field("size", pa.string(), nullable=False),
        pa.field("trade_id", pa.string(), nullable=False),
        pa.field("method", pa.string(), nullable=False),
        pa.field("liquidated_user_address", pa.string()),
    ],
    "funding_payment": [
        pa.field("amount_usdc", pa.string(), nullable=False),
        pa.field("position_size_base", pa.string(), nullable=False),
        pa.field("funding_rate", pa.string(), nullable=False),
    ],
    "account_order_update": [
        pa.field("venue_order_id", pa.string(), nullable=False),
        pa.field("client_order_id", pa.string()),
        pa.field("side", pa.string(), nullable=False),
        pa.field("limit_price", pa.string(), nullable=False),
        pa.field("remaining_size", pa.string(), nullable=False),
        pa.field("original_size", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("reduce_only", pa.bool_(), nullable=False),
    ],
    "account_fill": [
        pa.field("venue_order_id", pa.string(), nullable=False),
        pa.field("trade_id", pa.string(), nullable=False),
        pa.field("side", pa.string(), nullable=False),
        pa.field("price", pa.string(), nullable=False),
        pa.field("size", pa.string(), nullable=False),
        pa.field("liquidity", pa.string(), nullable=False),
        pa.field("fee", pa.string(), nullable=False),
        pa.field("fee_token", pa.string(), nullable=False),
        pa.field("closed_pnl", pa.string(), nullable=False),
        pa.field("transaction_hash", pa.string(), nullable=False),
    ],
    "position_snapshot": [
        pa.field("size_base", pa.string(), nullable=False),
        pa.field("entry_price", pa.string()),
        pa.field("unrealized_pnl_usd", pa.string(), nullable=False),
        pa.field("leverage", pa.string(), nullable=False),
        pa.field("liquidation_price", pa.string()),
        pa.field("margin_used_usd", pa.string()),
    ],
}


def parquet_schema(event_type: str) -> pa.Schema:
    try:
        fields = EVENT_FIELDS[event_type]
    except KeyError as exc:
        raise ValueError(f"no Parquet schema for event type: {event_type}") from exc
    return pa.schema(
        [*COMMON_FIELDS, *fields],
        metadata={
            b"aiquanttrader.schema_version": b"1",
            b"aiquanttrader.event_type": event_type.encode("ascii"),
        },
    )


def _event_row(event: MarketEvent, event_order: int) -> dict[str, Any]:
    payload = event.model_dump(mode="json")
    header = payload.pop("header")
    event_type = str(payload.pop("event_type"))
    instrument_id = header.get("instrument_id")
    row: dict[str, Any] = {
        "schema_version": int(header["schema_version"]),
        "event_order": event_order,
        "event_id": header["event_id"],
        "venue": header["venue"],
        "instrument_id": instrument_id,
        "event_ts_ns": int(header["event_ts_ns"]),
        "receive_ts_ns": int(header["receive_ts_ns"]),
        "connection_id": header["connection_id"],
        "source": header["source"],
        "event_ts_source": header["event_ts_source"],
        "source_record_id": header.get("source_record_id"),
    }
    if event_type == "account_liquidation":
        row["account_address"] = header.get("account_address")
    row.update(payload)
    return row


def _partition_path(
    output_root: Path,
    source: RawSegmentManifest,
    event_type: str,
) -> Path:
    instant = datetime.fromtimestamp(source.started_at_ns / 1_000_000_000, tz=UTC)
    instrument = "account" if event_type == "account_liquidation" else source.instrument_id
    return output_root / Path(
        "normalized",
        "venue=HYPERLIQUID",
        f"channel={event_type}",
        f"instrument={instrument}",
        f"date={instant:%Y-%m-%d}",
        f"hour={instant:%H}",
        f"{source.compressed_sha256}-{event_type}.parquet",
    )


def _write_parquet_immutable(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(8)}.partial")
    try:
        pq.write_table(
            table,
            partial,
            compression="zstd",
            compression_level=9,
            use_dictionary=False,
            write_statistics=True,
            version="2.6",
            data_page_version="1.0",
            row_group_size=65_536,
        )
        descriptor = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if path.exists():
            if sha256_file(path) != sha256_file(partial):
                raise FileExistsError(f"immutable Parquet partition differs: {path}")
            partial.unlink()
            return
        partial.rename(path)
        fsync_directory(path.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _quarantine_corrupt(
    segment_path: Path,
    manifest_path: Path,
    quarantine_root: Path,
) -> tuple[Path, ...]:
    moved: list[Path] = []
    quarantine_root.mkdir(parents=True, exist_ok=True)
    for source in (segment_path, manifest_path):
        if not source.exists():
            continue
        target = quarantine_root / source.name
        if target.exists():
            target = target.with_name(f"{target.name}.{sha256_file(source)[:8]}")
        source.rename(target)
        fsync_directory(target.parent)
        moved.append(target)
    return tuple(moved)


def normalize_segment(
    segment_path: Path,
    *,
    output_root: Path,
    quarantine_root: Path,
) -> NormalizationResult:
    output_root = output_root.resolve()
    quarantine_root = quarantine_root.resolve()
    try:
        reader = RawSegmentReader(segment_path)
        reader.verify()
    except (OSError, RawSegmentError, zstandard.ZstdError) as exc:
        manifest_path = segment_path.with_name(
            segment_path.name.removesuffix(".raw.zst") + ".manifest.json"
        )
        moved = _quarantine_corrupt(segment_path, manifest_path, quarantine_root)
        raise QuarantinedSegmentError(f"raw segment quarantined: {exc}", moved) from exc

    tracker = IntegrityTracker()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_frames = 0
    event_order = 0
    for record in reader.records():
        try:
            parsed = parse_frame(record.payload, record.metadata)
            events = tracker.observe_frame(parsed, record.metadata)
        except (ProtocolError, ValidationError) as exc:
            tracker.record_parse_failure(exc, record.metadata)
            excluded_frames += 1
            continue
        for event in events:
            grouped[event.event_type].append(_event_row(event, event_order))
            event_order += 1

    files: list[NormalizedFileManifest] = []
    for event_type in sorted(grouped):
        rows = grouped[event_type]
        table = pa.Table.from_pylist(rows, schema=parquet_schema(event_type))
        path = _partition_path(output_root, reader.manifest, event_type)
        _write_parquet_immutable(path, table)
        files.append(
            NormalizedFileManifest(
                event_type=event_type,
                relative_path=path.relative_to(output_root.resolve()).as_posix(),
                row_count=table.num_rows,
                byte_count=path.stat().st_size,
                file_sha256=sha256_file(path),
            )
        )

    manifest = NormalizedSegmentManifest(
        source_segment_id=reader.manifest.segment_id,
        source_segment_sha256=reader.manifest.compressed_sha256,
        normalizer_version=NORMALIZER_VERSION,
        files=tuple(files),
        issues=tuple(tracker.issues),
        event_count=event_order,
        excluded_frame_count=excluded_frames,
        created_at=reader.manifest.created_at,
    )
    manifest_path = output_root / Path(
        "normalized",
        "manifests",
        f"{reader.manifest.segment_id}.normalized.manifest.json",
    )
    content = manifest.canonical_bytes() + b"\n"
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise FileExistsError(
                f"normalized manifest differs from immutable target: {manifest_path}"
            )
    else:
        atomic_write_bytes(manifest_path, content)
    return NormalizationResult(manifest_path, manifest)


def load_normalized_manifest(path: Path) -> NormalizedSegmentManifest:
    try:
        return NormalizedSegmentManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise DatasetQualityError(f"invalid normalized manifest {path}: {exc}") from exc


def validate_normalized_files(manifest: NormalizedSegmentManifest, root: Path) -> None:
    for file in manifest.files:
        path = root / file.relative_path
        if not path.is_file():
            raise DatasetQualityError(f"normalized file is missing: {path}")
        if path.stat().st_size != file.byte_count or sha256_file(path) != file.file_sha256:
            raise DatasetQualityError(f"normalized file integrity mismatch: {path}")


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
