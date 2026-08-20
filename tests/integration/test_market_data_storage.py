from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from aiquanttrader.domain.data import (
    DataQualityPolicy,
    GapClassification,
    QualityIssueKind,
    RawSegmentManifest,
    SegmentFinalizationReason,
)
from aiquanttrader.market_data.catalog import ManifestCatalog
from aiquanttrader.market_data.normalizer import NormalizationBatch, NormalizationWorker
from aiquanttrader.market_data.raw import (
    FinalizedSegment,
    RawSegmentReader,
    RawSegmentWriter,
    quarantine_incomplete_segments,
)
from aiquanttrader.market_data.storage import (
    DatasetQualityError,
    QuarantinedSegmentError,
    build_dataset_manifest,
    load_normalized_manifest,
    normalize_segment,
    parquet_schema,
    validate_normalized_files,
)

NOW_MS = 1_700_000_000_000
NOW_NS = NOW_MS * 1_000_000


def payload(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def sample_payloads() -> tuple[bytes, ...]:
    book = payload(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": NOW_MS,
                "levels": [
                    [{"px": "99999", "sz": "2", "n": 2}],
                    [{"px": "100001", "sz": "3", "n": 3}],
                ],
            },
        }
    )
    trade = payload(
        {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "side": "B",
                    "px": "100000",
                    "sz": "0.01",
                    "hash": "0xabc",
                    "tid": 1,
                    "time": NOW_MS,
                }
            ],
        }
    )
    return book, trade, trade, b"not-json"


def make_segment(root: Path, *, connection: str = "ws-storage") -> FinalizedSegment:
    writer = RawSegmentWriter(
        root,
        network="mainnet",
        connection_id=connection,
        started_at_ns=NOW_NS,
        sync_every_records=1,
    )
    for index, frame in enumerate(sample_payloads()):
        writer.append(
            frame,
            receive_ts_ns=NOW_NS + index + 1,
            monotonic_ts_ns=index + 1,
        )
    return writer.finalize(SegmentFinalizationReason.ROTATION)


def test_raw_archive_round_trip_preserves_exact_payloads(tmp_path: Path) -> None:
    segment = make_segment(tmp_path)
    reader = RawSegmentReader(segment.segment_path)

    reader.verify()
    records = tuple(reader.records())
    assert tuple(record.payload for record in records) == sample_payloads()
    assert reader.manifest.record_count == 4
    assert reader.manifest.payload_bytes == sum(map(len, sample_payloads()))
    assert not list(tmp_path.rglob("*.partial"))


def test_normalization_is_deterministic_and_accounts_for_exclusions(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    first = normalize_segment(
        segment.segment_path,
        output_root=tmp_path / "first",
        quarantine_root=tmp_path / "first-quarantine",
    )
    second = normalize_segment(
        segment.segment_path,
        output_root=tmp_path / "second",
        quarantine_root=tmp_path / "second-quarantine",
    )

    assert first.manifest.canonical_bytes() == second.manifest.canonical_bytes()
    assert first.manifest.event_count == 2
    assert first.manifest.excluded_frame_count == 1
    assert [issue.kind for issue in first.manifest.issues] == [
        QualityIssueKind.DUPLICATE,
        QualityIssueKind.SCHEMA_ERROR,
    ]
    assert [item.file_sha256 for item in first.manifest.files] == [
        item.file_sha256 for item in second.manifest.files
    ]
    for item in first.manifest.files:
        table = pq.read_table(tmp_path / "first" / item.relative_path)
        assert table.num_rows == item.row_count


def test_corrupt_segment_is_quarantined_before_normalization(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    with segment.segment_path.open("r+b") as stream:
        stream.seek(0)
        first = stream.read(1)
        stream.seek(0)
        stream.write(bytes([first[0] ^ 0xFF]))

    with pytest.raises(QuarantinedSegmentError) as raised:
        normalize_segment(
            segment.segment_path,
            output_root=tmp_path / "output",
            quarantine_root=tmp_path / "quarantine",
        )

    assert len(raised.value.paths) == 2
    assert not segment.segment_path.exists()
    assert not segment.manifest_path.exists()


def test_recovery_only_quarantines_incomplete_raw_artifacts(tmp_path: Path) -> None:
    partial = tmp_path / "raw" / "venue=HYPERLIQUID" / "orphan.raw.zst.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    unrelated = tmp_path / "normalized" / "x.manifest.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("{}", encoding="utf-8")

    moved = quarantine_incomplete_segments(tmp_path)

    assert len(moved) == 1
    assert moved[0].read_bytes() == b"partial"
    assert unrelated.exists()


def _validated_copy(manifest: RawSegmentManifest, **updates: object) -> RawSegmentManifest:
    values = manifest.model_dump(mode="json")
    values.update(updates)
    return RawSegmentManifest.model_validate(values)


def test_dataset_admission_classifies_gaps_and_enforces_policy(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    normalized = normalize_segment(
        segment.segment_path,
        output_root=tmp_path / "output",
        quarantine_root=tmp_path / "quarantine",
    ).manifest
    clean = normalized.model_copy(update={"issues": ()})
    second_raw = _validated_copy(
        segment.manifest,
        segment_id="second-segment",
        connection_id="ws-second",
        started_at_ns=segment.manifest.ended_at_ns + 5_000_000_000,
        ended_at_ns=segment.manifest.ended_at_ns + 6_000_000_000,
        compressed_sha256="1" * 64,
        records_sha256="2" * 64,
        relative_path="raw/second.raw.zst",
    )
    second_normalized = clean.model_copy(
        update={
            "source_segment_id": second_raw.segment_id,
            "source_segment_sha256": second_raw.compressed_sha256,
        }
    )
    admitted = build_dataset_manifest(
        [(segment.manifest, clean), (second_raw, second_normalized)],
        DataQualityPolicy(max_classified_gap_ns=10_000_000_000),
    )
    assert admitted.gaps[0].classification is GapClassification.PLANNED_ROTATION
    assert admitted.market_wide_liquidations_available is False

    unexplained = _validated_copy(
        segment.manifest, finalization_reason=SegmentFinalizationReason.ERROR
    )
    with pytest.raises(DatasetQualityError, match="unexplained"):
        build_dataset_manifest(
            [(unexplained, clean), (second_raw, second_normalized)], DataQualityPolicy()
        )


def test_dataset_rejects_quality_issue_over_policy(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    normalized = normalize_segment(
        segment.segment_path,
        output_root=tmp_path / "output",
        quarantine_root=tmp_path / "quarantine",
    ).manifest
    with pytest.raises(DatasetQualityError, match="duplicate"):
        build_dataset_manifest(
            [(segment.manifest, normalized)], DataQualityPolicy(max_schema_errors=1)
        )


def test_independent_worker_normalizes_pending_segments_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_segment(tmp_path / "data")
    with ManifestCatalog(tmp_path / "state" / "normalized.duckdb") as catalog:
        worker = NormalizationWorker(tmp_path / "data", catalog)
        first = worker.run_once()
        monkeypatch.setattr(
            "aiquanttrader.market_data.normalizer.load_segment_manifest",
            lambda _: pytest.fail("cataloged immutable segments must not be reloaded"),
        )
        second = worker.run_once()
        count = catalog.connection.execute("SELECT count(*) FROM normalized_segments").fetchone()

    assert first.normalized == 1
    assert first.already_complete == 0
    assert second.normalized == 0
    assert second.already_complete == 1
    assert count == (1,)


def test_normalizer_never_quarantines_the_recorders_active_partial(tmp_path: Path) -> None:
    data = tmp_path / "data"
    active = RawSegmentWriter(
        data,
        network="mainnet",
        connection_id="ws-active-recorder",
        started_at_ns=NOW_NS,
        sync_every_records=1,
    )
    active.append(b'{"channel":"pong"}', receive_ts_ns=NOW_NS + 1, monotonic_ts_ns=1)

    try:
        with ManifestCatalog(tmp_path / "state" / "normalized.duckdb") as catalog:
            batch = NormalizationWorker(data, catalog).run_once()

        assert batch == NormalizationBatch(0, 0, 0, 0)
        assert active.partial_path.is_file()
        assert not (data / "quarantine").exists()
    finally:
        active.abort()

    assert quarantine_incomplete_segments(data)


def test_normalized_artifacts_are_immutable_and_validated(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    root = tmp_path / "output"
    first = normalize_segment(
        segment.segment_path, output_root=root, quarantine_root=tmp_path / "quarantine"
    )
    assert (
        normalize_segment(
            segment.segment_path, output_root=root, quarantine_root=tmp_path / "quarantine"
        ).manifest
        == first.manifest
    )
    validate_normalized_files(first.manifest, root)

    file = first.manifest.files[0]
    path = root / file.relative_path
    path.unlink()
    with pytest.raises(DatasetQualityError, match="missing"):
        validate_normalized_files(first.manifest, root)

    first.manifest_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(DatasetQualityError, match="invalid normalized"):
        load_normalized_manifest(first.manifest_path)
    with pytest.raises(ValueError, match="no Parquet schema"):
        parquet_schema("unknown")


def test_dataset_rejects_classified_gap_over_limit(tmp_path: Path) -> None:
    segment = make_segment(tmp_path / "capture")
    normalized = normalize_segment(
        segment.segment_path,
        output_root=tmp_path / "output",
        quarantine_root=tmp_path / "quarantine",
    ).manifest.model_copy(update={"issues": ()})
    second = _validated_copy(
        segment.manifest,
        segment_id="late-segment",
        connection_id="ws-late",
        started_at_ns=segment.manifest.ended_at_ns + 31_000_000_000,
        ended_at_ns=segment.manifest.ended_at_ns + 32_000_000_000,
        compressed_sha256="3" * 64,
        records_sha256="4" * 64,
        relative_path="raw/late.raw.zst",
    )
    second_normalized = normalized.model_copy(
        update={
            "source_segment_id": second.segment_id,
            "source_segment_sha256": second.compressed_sha256,
        }
    )
    with pytest.raises(DatasetQualityError, match="exceeds"):
        build_dataset_manifest(
            [(segment.manifest, normalized), (second, second_normalized)], DataQualityPolicy()
        )
