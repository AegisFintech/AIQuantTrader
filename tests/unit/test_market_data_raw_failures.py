from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader.domain.data import RawSegmentManifest, SegmentFinalizationReason
from aiquanttrader.market_data.io import atomic_replace_bytes, atomic_write_bytes
from aiquanttrader.market_data.raw import (
    RawSegmentError,
    RawSegmentReader,
    RawSegmentWriter,
    load_segment_manifest,
    quarantine_incomplete_segments,
    segment_manifest_path,
)


def writer(root: Path, *, start: int = 100, connection: str = "ws-raw") -> RawSegmentWriter:
    return RawSegmentWriter(
        root,
        network="mainnet",
        connection_id=connection,
        started_at_ns=start,
        sync_every_records=1,
        max_frame_bytes=16,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"started_at_ns": -1},
        {"sync_every_records": 0},
        {"max_frame_bytes": 0},
    ],
)
def test_writer_configuration_rejects_invalid_bounds(
    tmp_path: Path, kwargs: dict[str, int]
) -> None:
    values = {
        "network": "mainnet",
        "connection_id": "ws-invalid",
        "started_at_ns": 1,
        "sync_every_records": 1,
        "max_frame_bytes": 1,
        **kwargs,
    }
    with pytest.raises(ValueError):
        RawSegmentWriter(tmp_path, **values)  # type: ignore[arg-type]


def test_writer_rejects_bad_frames_and_closed_operations(tmp_path: Path) -> None:
    first = writer(tmp_path)
    with pytest.raises(RawSegmentError, match="maximum"):
        first.append(b"x" * 17, receive_ts_ns=101, monotonic_ts_ns=1)
    with pytest.raises(RawSegmentError, match="precedes"):
        first.append(b"x", receive_ts_ns=99, monotonic_ts_ns=1)
    first.append(b"{}", receive_ts_ns=101, monotonic_ts_ns=1)
    finalized = first.finalize(SegmentFinalizationReason.SHUTDOWN)
    with pytest.raises(RuntimeError, match="finalized"):
        first.append(b"{}", receive_ts_ns=102, monotonic_ts_ns=2)
    with pytest.raises(RuntimeError, match="already closed"):
        first.finalize(SegmentFinalizationReason.SHUTDOWN)
    first.abort()

    assert segment_manifest_path(finalized.segment_path) == finalized.manifest_path
    with pytest.raises(ValueError, match="not a raw"):
        segment_manifest_path(tmp_path / "file.txt")


def test_duplicate_segment_identity_and_context_abort_are_recovered(tmp_path: Path) -> None:
    active = writer(tmp_path)
    with pytest.raises(FileExistsError, match="identity"):
        writer(tmp_path)
    active.abort()
    assert len(quarantine_incomplete_segments(tmp_path)) == 1

    with writer(tmp_path, start=200, connection="ws-context"):
        pass
    assert len(quarantine_incomplete_segments(tmp_path)) == 1


def test_manifest_and_segment_mismatches_fail_verification(tmp_path: Path) -> None:
    active = writer(tmp_path)
    active.append(b"{}", receive_ts_ns=101, monotonic_ts_ns=1)
    finalized = active.finalize(SegmentFinalizationReason.ROTATION)
    original = finalized.manifest.model_dump(mode="json")

    wrong_size = RawSegmentManifest.model_validate(
        {**original, "compressed_bytes": finalized.manifest.compressed_bytes + 1}
    )
    finalized.manifest_path.write_bytes(wrong_size.canonical_bytes())
    with pytest.raises(RawSegmentError, match="byte count"):
        RawSegmentReader(finalized.segment_path).verify()

    wrong_digest = RawSegmentManifest.model_validate({**original, "compressed_sha256": "0" * 64})
    finalized.manifest_path.write_bytes(wrong_digest.canonical_bytes())
    with pytest.raises(RawSegmentError, match="digest"):
        RawSegmentReader(finalized.segment_path).verify()

    finalized.manifest_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RawSegmentError, match="invalid segment manifest"):
        load_segment_manifest(finalized.manifest_path)


def test_orphan_raw_and_manifest_files_are_quarantined(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "orphan.raw.zst"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw")
    manifest = tmp_path / "raw" / "missing.manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")

    moved = quarantine_incomplete_segments(tmp_path)
    assert len(moved) == 2
    assert not raw.exists()
    assert not manifest.exists()


def test_atomic_immutable_and_mutable_writes(tmp_path: Path) -> None:
    immutable = tmp_path / "immutable"
    atomic_write_bytes(immutable, b"one")
    with pytest.raises(FileExistsError):
        atomic_write_bytes(immutable, b"two")
    assert immutable.read_bytes() == b"one"

    state = tmp_path / "state"
    atomic_replace_bytes(state, b"one")
    atomic_replace_bytes(state, b"two")
    assert state.read_bytes() == b"two"
