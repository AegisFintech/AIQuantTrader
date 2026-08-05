from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aiquanttrader.domain.data import (
    DatasetGap,
    DatasetManifest,
    GapClassification,
    NormalizedFileManifest,
    NormalizedSegmentManifest,
    RawSegmentManifest,
    SegmentFinalizationReason,
    TardisFileManifest,
)


def raw_values() -> dict[str, object]:
    return {
        "segment_id": "segment-1",
        "network": "mainnet",
        "relative_path": "raw/segment.raw.zst",
        "connection_id": "ws-one",
        "started_at_ns": 1,
        "ended_at_ns": 2,
        "record_count": 0,
        "payload_bytes": 0,
        "compressed_bytes": 1,
        "compressed_sha256": "a" * 64,
        "records_sha256": "b" * 64,
        "recorder_version": "test-v1",
        "finalization_reason": SegmentFinalizationReason.ROTATION,
        "created_at": datetime.now(UTC),
    }


def test_paths_and_raw_time_ranges_are_fail_closed() -> None:
    assert RawSegmentManifest.model_validate(raw_values()).ended_at_ns == 2
    for path in ("/absolute", "raw/../escape", "bad path"):
        with pytest.raises(ValidationError):
            RawSegmentManifest.model_validate({**raw_values(), "relative_path": path})
    with pytest.raises(ValidationError, match="end precedes"):
        RawSegmentManifest.model_validate({**raw_values(), "started_at_ns": 3, "ended_at_ns": 2})
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawSegmentManifest.model_validate({**raw_values(), "created_at": datetime(2024, 1, 1)})


def test_normalized_manifest_validates_rows_paths_and_timezone() -> None:
    file = NormalizedFileManifest(
        event_type="trade",
        relative_path="normalized/trade.parquet",
        row_count=1,
        byte_count=1,
        file_sha256="c" * 64,
    )
    values = {
        "source_segment_id": "segment-1",
        "source_segment_sha256": "a" * 64,
        "normalizer_version": "test-v1",
        "files": [file.model_dump(mode="json")],
        "event_count": 1,
        "excluded_frame_count": 0,
        "created_at": datetime.now(UTC),
    }
    assert NormalizedSegmentManifest.model_validate(values).event_count == 1
    with pytest.raises(ValidationError, match="rows"):
        NormalizedSegmentManifest.model_validate({**values, "event_count": 2})
    with pytest.raises(ValidationError, match="unique"):
        NormalizedSegmentManifest.model_validate(
            {**values, "files": [file, file], "event_count": 2}
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        NormalizedSegmentManifest.model_validate({**values, "created_at": datetime(2024, 1, 1)})


def test_dataset_gap_and_manifest_invariants() -> None:
    gap = DatasetGap(
        start_ts_ns=1,
        end_ts_ns=2,
        duration_ns=1,
        classification=GapClassification.PLANNED_ROTATION,
        previous_segment_id="one",
        next_segment_id="two",
    )
    with pytest.raises(ValidationError, match="duration"):
        DatasetGap.model_validate({**gap.model_dump(), "duration_ns": 0})
    with pytest.raises(ValidationError, match="precedes"):
        DatasetGap.model_validate(
            {**gap.model_dump(), "start_ts_ns": 3, "end_ts_ns": 2, "duration_ns": 0}
        )

    values = {
        "dataset_id": "d" * 64,
        "normalized_manifest_sha256s": ["e" * 64],
        "policy_sha256": "f" * 64,
        "gaps": [],
        "created_at": datetime.now(UTC),
    }
    assert DatasetManifest.model_validate(values).market_wide_liquidations_available is False
    with pytest.raises(ValidationError, match="at least one"):
        DatasetManifest.model_validate({**values, "normalized_manifest_sha256s": []})
    with pytest.raises(ValidationError, match="timezone-aware"):
        DatasetManifest.model_validate({**values, "created_at": datetime(2024, 1, 1)})


def test_tardis_manifest_requires_aware_creation_time() -> None:
    values = {
        "data_type": "trades",
        "date": "2024-10-29",
        "relative_path": "historical/BTC.csv.gz",
        "byte_count": 1,
        "compressed_sha256": "a" * 64,
        "row_count": 1,
        "source_url": "https://datasets.tardis.dev/file",
        "created_at": datetime(2024, 1, 1),
    }
    with pytest.raises(ValidationError, match="timezone-aware"):
        TardisFileManifest.model_validate(values)
