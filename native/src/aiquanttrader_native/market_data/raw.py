"""Framed Zstandard raw archive with atomic manifests and quarantine recovery."""

from __future__ import annotations

import hashlib
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal, Self

import zstandard

from aiquanttrader_native.domain.base import canonical_json_bytes
from aiquanttrader_native.domain.data import (
    RawFrameMetadata,
    RawSegmentManifest,
    SegmentFinalizationReason,
)
from aiquanttrader_native.market_data.io import atomic_write_bytes, fsync_directory, sha256_file

FORMAT_MAGIC = b"AQTRAW01"
FOOTER_MARKER = 0xFFFFFFFF
FOOTER_MAGIC = b"AQTEND01"
UINT32 = struct.Struct("!I")
FOOTER = struct.Struct("!Q32s8s")
DEFAULT_RECORDER_VERSION = "market-data-v1"


class RawSegmentError(ValueError):
    """A raw segment or manifest failed structural or cryptographic validation."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    metadata: RawFrameMetadata
    payload: bytes


@dataclass(frozen=True, slots=True)
class FinalizedSegment:
    segment_path: Path
    manifest_path: Path
    manifest: RawSegmentManifest


def segment_manifest_path(segment_path: Path) -> Path:
    suffix = ".raw.zst"
    if not segment_path.name.endswith(suffix):
        raise ValueError(f"not a raw segment path: {segment_path}")
    return segment_path.with_name(segment_path.name.removesuffix(suffix) + ".manifest.json")


def _utc_partition(timestamp_ns: int) -> tuple[str, str]:
    instant = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
    return instant.strftime("%Y-%m-%d"), instant.strftime("%H")


class RawSegmentWriter:
    """Append exact inbound payloads before parsing or normalization."""

    def __init__(
        self,
        root: Path,
        *,
        network: Literal["testnet", "mainnet"],
        connection_id: str,
        started_at_ns: int,
        sync_every_records: int = 100,
        max_frame_bytes: int = 8_388_608,
        recorder_version: str = DEFAULT_RECORDER_VERSION,
    ) -> None:
        if started_at_ns < 0:
            raise ValueError("segment start timestamp cannot be negative")
        if sync_every_records < 1:
            raise ValueError("sync interval must be positive")
        if max_frame_bytes < 1:
            raise ValueError("maximum frame size must be positive")

        self.root = root.resolve()
        self.network = network
        self.connection_id = connection_id
        self.started_at_ns = started_at_ns
        self.sync_every_records = sync_every_records
        self.max_frame_bytes = max_frame_bytes
        self.recorder_version = recorder_version
        date, hour = _utc_partition(started_at_ns)
        self.segment_id = f"{date}T{hour}-{started_at_ns}-{connection_id}"
        relative = Path(
            "raw",
            "venue=HYPERLIQUID",
            f"network={network}",
            f"date={date}",
            f"hour={hour}",
            f"{self.segment_id}.raw.zst",
        )
        self.segment_path = self.root / relative
        self.partial_path = self.segment_path.with_name(f"{self.segment_path.name}.partial")
        self.manifest_path = segment_manifest_path(self.segment_path)
        self.segment_path.parent.mkdir(parents=True, exist_ok=True)
        if self.segment_path.exists() or self.partial_path.exists() or self.manifest_path.exists():
            raise FileExistsError(f"segment identity already exists: {self.segment_id}")

        descriptor = os.open(
            self.partial_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o640,
        )
        self._raw: BinaryIO = os.fdopen(descriptor, "wb", buffering=0)
        compressor = zstandard.ZstdCompressor(
            level=9,
            write_checksum=True,
            write_content_size=False,
            write_dict_id=False,
        )
        self._stream = compressor.stream_writer(self._raw, closefd=False)
        self._stream.write(FORMAT_MAGIC)
        self._records_digest = hashlib.sha256()
        self._record_count = 0
        self._payload_bytes = 0
        self._last_receive_ts_ns = started_at_ns
        self._closed = False

    @property
    def record_count(self) -> int:
        return self._record_count

    def append(
        self,
        payload: bytes,
        *,
        receive_ts_ns: int,
        monotonic_ts_ns: int,
        subscription_id: str = "public-btc",
        transport: Literal["text", "binary"] = "text",
    ) -> RawFrameMetadata:
        if self._closed:
            raise RuntimeError("cannot append to a finalized segment")
        if len(payload) > self.max_frame_bytes:
            raise RawSegmentError(
                f"frame exceeds configured maximum: {len(payload)} > {self.max_frame_bytes}"
            )
        if receive_ts_ns < self.started_at_ns:
            raise RawSegmentError("frame receive time precedes segment start")
        metadata = RawFrameMetadata(
            receive_ts_ns=receive_ts_ns,
            monotonic_ts_ns=monotonic_ts_ns,
            connection_id=self.connection_id,
            subscription_id=subscription_id,
            transport=transport,
            payload_size=len(payload),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            recorder_version=self.recorder_version,
        )
        metadata_bytes = canonical_json_bytes(metadata.model_dump(mode="json"))
        block = (
            UINT32.pack(len(metadata_bytes)) + metadata_bytes + UINT32.pack(len(payload)) + payload
        )
        self._stream.write(block)
        self._stream.flush(zstandard.FLUSH_BLOCK)
        self._records_digest.update(block)
        self._record_count += 1
        self._payload_bytes += len(payload)
        self._last_receive_ts_ns = max(self._last_receive_ts_ns, receive_ts_ns)
        if self._record_count % self.sync_every_records == 0:
            os.fdatasync(self._raw.fileno())
        return metadata

    def finalize(self, reason: SegmentFinalizationReason) -> FinalizedSegment:
        if self._closed:
            raise RuntimeError("segment is already closed")
        records_sha = self._records_digest.digest()
        self._stream.write(FOOTER_MARKER.to_bytes(4, "big"))
        self._stream.write(FOOTER.pack(self._record_count, records_sha, FOOTER_MAGIC))
        self._stream.flush(zstandard.FLUSH_FRAME)
        self._stream.close()  # type: ignore[no-untyped-call]
        os.fsync(self._raw.fileno())
        self._raw.close()
        self._closed = True
        if self.segment_path.exists():
            raise FileExistsError(f"immutable segment already exists: {self.segment_path}")
        self.partial_path.rename(self.segment_path)
        fsync_directory(self.segment_path.parent)

        compressed_bytes = self.segment_path.stat().st_size
        compressed_sha = sha256_file(self.segment_path)
        manifest = RawSegmentManifest(
            segment_id=self.segment_id,
            network=self.network,
            relative_path=self.segment_path.relative_to(self.root).as_posix(),
            connection_id=self.connection_id,
            started_at_ns=self.started_at_ns,
            ended_at_ns=self._last_receive_ts_ns,
            record_count=self._record_count,
            payload_bytes=self._payload_bytes,
            compressed_bytes=compressed_bytes,
            compressed_sha256=compressed_sha,
            records_sha256=records_sha.hex(),
            recorder_version=self.recorder_version,
            finalization_reason=reason,
            created_at=datetime.now(UTC),
        )
        atomic_write_bytes(self.manifest_path, manifest.canonical_bytes() + b"\n")
        return FinalizedSegment(self.segment_path, self.manifest_path, manifest)

    def abort(self) -> None:
        """Close without a footer; recovery will quarantine the partial file."""

        if self._closed:
            return
        try:
            self._stream.close()  # type: ignore[no-untyped-call]
        finally:
            self._raw.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._closed:
            self.abort()


def load_segment_manifest(path: Path) -> RawSegmentManifest:
    try:
        return RawSegmentManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RawSegmentError(f"invalid segment manifest {path}: {exc}") from exc


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RawSegmentError(f"truncated raw segment: expected {remaining} more bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class RawSegmentReader:
    def __init__(self, segment_path: Path, manifest_path: Path | None = None) -> None:
        self.segment_path = segment_path
        self.manifest_path = manifest_path or segment_manifest_path(segment_path)
        self.manifest = load_segment_manifest(self.manifest_path)

    def verify(self) -> None:
        if not self.segment_path.is_file():
            raise RawSegmentError(f"raw segment is missing: {self.segment_path}")
        if self.segment_path.stat().st_size != self.manifest.compressed_bytes:
            raise RawSegmentError("compressed byte count does not match manifest")
        if sha256_file(self.segment_path) != self.manifest.compressed_sha256:
            raise RawSegmentError("compressed segment digest does not match manifest")
        record_count = 0
        payload_bytes = 0
        for record in self.records():
            record_count += 1
            payload_bytes += len(record.payload)
        if record_count != self.manifest.record_count:
            raise RawSegmentError("record count does not match manifest")
        if payload_bytes != self.manifest.payload_bytes:
            raise RawSegmentError("payload byte count does not match manifest")

    def records(self) -> Iterator[RawRecord]:
        digest = hashlib.sha256()
        record_count = 0
        with (
            self.segment_path.open("rb") as compressed,
            zstandard.ZstdDecompressor().stream_reader(compressed) as stream,
        ):
            if _read_exact(stream, len(FORMAT_MAGIC)) != FORMAT_MAGIC:
                raise RawSegmentError("unsupported raw segment magic")
            while True:
                length_bytes = _read_exact(stream, UINT32.size)
                metadata_length = UINT32.unpack(length_bytes)[0]
                if metadata_length == FOOTER_MARKER:
                    footer_count, footer_digest, footer_magic = FOOTER.unpack(
                        _read_exact(stream, FOOTER.size)
                    )
                    if footer_magic != FOOTER_MAGIC:
                        raise RawSegmentError("invalid raw segment footer")
                    if footer_count != record_count:
                        raise RawSegmentError("footer record count mismatch")
                    if footer_digest != digest.digest():
                        raise RawSegmentError("footer record digest mismatch")
                    if footer_digest.hex() != self.manifest.records_sha256:
                        raise RawSegmentError("manifest record digest mismatch")
                    if stream.read(1):
                        raise RawSegmentError("trailing data follows raw segment footer")
                    break
                if metadata_length > 1_048_576:
                    raise RawSegmentError("raw frame metadata exceeds safety limit")
                metadata_bytes = _read_exact(stream, metadata_length)
                payload_length_bytes = _read_exact(stream, UINT32.size)
                payload_length = UINT32.unpack(payload_length_bytes)[0]
                if payload_length > 67_108_864:
                    raise RawSegmentError("raw payload exceeds format safety limit")
                payload = _read_exact(stream, payload_length)
                block = length_bytes + metadata_bytes + payload_length_bytes + payload
                digest.update(block)
                try:
                    metadata = RawFrameMetadata.model_validate_json(metadata_bytes)
                except ValueError as exc:
                    raise RawSegmentError(f"invalid raw frame metadata: {exc}") from exc
                if metadata.payload_size != payload_length:
                    raise RawSegmentError("payload length does not match frame metadata")
                if hashlib.sha256(payload).hexdigest() != metadata.payload_sha256:
                    raise RawSegmentError("payload digest does not match frame metadata")
                record_count += 1
                yield RawRecord(metadata, payload)


def quarantine_incomplete_segments(root: Path) -> tuple[Path, ...]:
    """Move partial and orphan artifacts out of the admissible raw tree."""

    root = root.resolve()
    quarantine_root = root / "quarantine" / "raw-incomplete"
    candidates: set[Path] = set()
    raw_root = root / "raw"
    if not raw_root.exists():
        return ()
    for path in raw_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(".partial") or (
            path.name.endswith(".raw.zst") and not segment_manifest_path(path).is_file()
        ):
            candidates.add(path)
        elif path.name.endswith(".manifest.json"):
            segment = path.with_name(path.name.removesuffix(".manifest.json") + ".raw.zst")
            if not segment.is_file():
                candidates.add(path)

    moved: list[Path] = []
    for source in sorted(candidates):
        relative = source.relative_to(root)
        target = quarantine_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(
                f"{target.name}.{hashlib.sha256(str(source).encode()).hexdigest()[:8]}"
            )
        source.rename(target)
        fsync_directory(target.parent)
        moved.append(target)
    return tuple(moved)
