"""Immutable raw, normalized, and dataset lineage contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from aiquanttrader.domain.base import DomainModel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SafeIdentifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("path must be relative and cannot contain traversal components")
    return value


RelativePath = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_./=:-]+$"),
    AfterValidator(_safe_relative_path),
]


class SegmentFinalizationReason(StrEnum):
    ROTATION = "rotation"
    DISCONNECT = "disconnect"
    SHUTDOWN = "shutdown"
    STALE_FEED = "stale_feed"
    DISK_PRESSURE = "disk_pressure"
    ERROR = "error"


class QualityIssueKind(StrEnum):
    SCHEMA_ERROR = "schema_error"
    DUPLICATE = "duplicate"
    TIMESTAMP_REGRESSION = "timestamp_regression"
    CROSSED_BOOK = "crossed_book"
    SILENCE = "silence"
    RECONNECT = "reconnect"
    CADENCE_ANOMALY = "cadence_anomaly"
    DISK_PRESSURE = "disk_pressure"
    UNEXPLAINED_GAP = "unexplained_gap"


class GapClassification(StrEnum):
    PLANNED_ROTATION = "planned_rotation"
    RECORDER_RESTART = "recorder_restart"
    VENUE_DISCONNECT = "venue_disconnect"
    STALE_FEED_RECOVERY = "stale_feed_recovery"
    DISK_PRESSURE = "disk_pressure"
    UNEXPLAINED = "unexplained"


class RawFrameMetadata(DomainModel):
    schema_version: Literal[1] = 1
    receive_ts_ns: int = Field(ge=0)
    monotonic_ts_ns: int = Field(ge=0)
    connection_id: SafeIdentifier
    subscription_id: SafeIdentifier
    transport: Literal["text", "binary"]
    payload_size: int = Field(ge=0)
    payload_sha256: Sha256
    recorder_version: SafeIdentifier


class RawSegmentManifest(DomainModel):
    schema_version: Literal[1] = 1
    segment_id: SafeIdentifier
    venue: Literal["HYPERLIQUID"] = "HYPERLIQUID"
    network: Literal["testnet", "mainnet"]
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    relative_path: RelativePath
    connection_id: SafeIdentifier
    started_at_ns: int = Field(ge=0)
    ended_at_ns: int = Field(ge=0)
    record_count: int = Field(ge=0)
    payload_bytes: int = Field(ge=0)
    compressed_bytes: int = Field(ge=0)
    compressed_sha256: Sha256
    records_sha256: Sha256
    recorder_version: SafeIdentifier
    finalization_reason: SegmentFinalizationReason
    created_at: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.ended_at_ns < self.started_at_ns:
            raise ValueError("segment end precedes segment start")
        if self.created_at.tzinfo is None:
            raise ValueError("segment creation time must be timezone-aware")
        return self


class QualityIssue(DomainModel):
    kind: QualityIssueKind
    receive_ts_ns: int = Field(ge=0)
    event_ts_ns: int | None = Field(default=None, ge=0)
    channel: Annotated[str, Field(min_length=1, max_length=64)]
    code: Annotated[str, Field(min_length=1, max_length=128)]
    payload_sha256: Sha256 | None = None


class NormalizedFileManifest(DomainModel):
    event_type: Annotated[str, Field(min_length=1, max_length=64)]
    relative_path: RelativePath
    row_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)
    file_sha256: Sha256


class NormalizedSegmentManifest(DomainModel):
    schema_version: Literal[1] = 1
    source_segment_id: SafeIdentifier
    source_segment_sha256: Sha256
    normalizer_version: SafeIdentifier
    files: tuple[NormalizedFileManifest, ...]
    issues: tuple[QualityIssue, ...] = ()
    event_count: int = Field(ge=0)
    excluded_frame_count: int = Field(ge=0)
    created_at: datetime

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("normalization time must be timezone-aware")
        if sum(item.row_count for item in self.files) != self.event_count:
            raise ValueError("normalized file rows must equal event count")
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("normalized file paths must be unique")
        return self


class DatasetGap(DomainModel):
    start_ts_ns: int = Field(ge=0)
    end_ts_ns: int = Field(ge=0)
    duration_ns: int = Field(ge=0)
    classification: GapClassification
    previous_segment_id: SafeIdentifier
    next_segment_id: SafeIdentifier

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        if self.end_ts_ns < self.start_ts_ns:
            raise ValueError("gap end precedes gap start")
        if self.end_ts_ns - self.start_ts_ns != self.duration_ns:
            raise ValueError("gap duration does not match endpoints")
        return self


class DatasetManifest(DomainModel):
    schema_version: Literal[1] = 1
    dataset_id: Sha256
    normalized_manifest_sha256s: tuple[Sha256, ...]
    policy_sha256: Sha256
    gaps: tuple[DatasetGap, ...]
    market_wide_liquidations_available: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("dataset creation time must be timezone-aware")
        if not self.normalized_manifest_sha256s:
            raise ValueError("dataset requires at least one normalized manifest")
        return self


class DataQualityPolicy(DomainModel):
    schema_version: Literal[1] = 1
    max_classified_gap_ns: int = Field(default=30_000_000_000, ge=0)
    max_schema_errors: int = Field(default=0, ge=0)
    max_crossed_books: int = Field(default=0, ge=0)
    max_timestamp_regressions: int = Field(default=0, ge=0)
    max_duplicates: int = Field(default=0, ge=0)
    reject_unexplained_gaps: bool = True


class TardisFileManifest(DomainModel):
    schema_version: Literal[1] = 1
    exchange: Literal["hyperliquid"] = "hyperliquid"
    data_type: Literal[
        "incremental_book_L2",
        "book_snapshot_25",
        "quotes",
        "trades",
        "derivative_ticker",
    ]
    symbol: Literal["BTC"] = "BTC"
    date: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    relative_path: RelativePath
    byte_count: int = Field(ge=1)
    compressed_sha256: Sha256
    gzip_valid: Literal[True] = True
    row_count: int = Field(ge=0)
    source_url: Annotated[str, StringConstraints(pattern=r"^https://datasets\.tardis\.dev/")]
    created_at: datetime

    @model_validator(mode="after")
    def created_at_must_be_aware(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("Tardis manifest time must be timezone-aware")
        return self


class RecorderState(DomainModel):
    schema_version: Literal[1] = 1
    status: Literal["starting", "connected", "reconnecting", "stopped", "failed"]
    environment: SafeIdentifier
    network: Literal["testnet", "mainnet"]
    connection_id: SafeIdentifier | None = None
    heartbeat_ts_ns: int = Field(ge=0)
    last_frame_ts_ns: int | None = Field(default=None, ge=0)
    current_segment_id: SafeIdentifier | None = None
    reconnect_count: int = Field(ge=0)
    last_error_code: Annotated[str, Field(min_length=1, max_length=128)] | None = None
