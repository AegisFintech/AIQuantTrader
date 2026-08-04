"""Single-writer DuckDB catalog for immutable market-data manifests."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb

from aiquanttrader_native.domain.data import (
    NormalizedSegmentManifest,
    RawSegmentManifest,
    TardisFileManifest,
)


class CatalogLockedError(RuntimeError):
    """Another process owns the catalog write lease."""


class ManifestCatalog:
    """Own the only write connection to the market-data catalog."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = self.lock_path.open("a+b")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            raise CatalogLockedError(f"catalog writer is already active: {self.path}") from exc
        try:
            self.connection = duckdb.connect(str(self.path))
            self._initialize()
        except BaseException:
            self._release_lock()
            raise

    def _initialize(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_segments (
                segment_id VARCHAR PRIMARY KEY,
                started_at_ns UBIGINT NOT NULL,
                ended_at_ns UBIGINT NOT NULL,
                record_count UBIGINT NOT NULL,
                compressed_sha256 VARCHAR NOT NULL UNIQUE,
                finalization_reason VARCHAR NOT NULL,
                manifest_json JSON NOT NULL
            );
            CREATE TABLE IF NOT EXISTS normalized_segments (
                source_segment_id VARCHAR PRIMARY KEY,
                source_segment_sha256 VARCHAR NOT NULL,
                event_count UBIGINT NOT NULL,
                excluded_frame_count UBIGINT NOT NULL,
                issue_count UBIGINT NOT NULL,
                manifest_json JSON NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tardis_files (
                exchange VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                sha256 VARCHAR NOT NULL UNIQUE,
                manifest_json JSON NOT NULL,
                PRIMARY KEY (exchange, data_type, symbol, date)
            );
            """
        )

    def register_raw(self, manifest: RawSegmentManifest) -> None:
        payload = manifest.canonical_bytes().decode("utf-8")
        self.connection.execute(
            """
            INSERT INTO raw_segments VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                manifest.segment_id,
                manifest.started_at_ns,
                manifest.ended_at_ns,
                manifest.record_count,
                manifest.compressed_sha256,
                manifest.finalization_reason.value,
                payload,
            ],
        )
        stored = self.connection.execute(
            "SELECT manifest_json FROM raw_segments WHERE segment_id = ?",
            [manifest.segment_id],
        ).fetchone()
        if stored is None or json.loads(stored[0]) != json.loads(payload):
            raise ValueError(f"catalog identity collision for raw segment {manifest.segment_id}")

    def register_normalized(self, manifest: NormalizedSegmentManifest) -> None:
        payload = manifest.canonical_bytes().decode("utf-8")
        self.connection.execute(
            """
            INSERT INTO normalized_segments VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                manifest.source_segment_id,
                manifest.source_segment_sha256,
                manifest.event_count,
                manifest.excluded_frame_count,
                len(manifest.issues),
                payload,
            ],
        )
        stored = self.connection.execute(
            "SELECT manifest_json FROM normalized_segments WHERE source_segment_id = ?",
            [manifest.source_segment_id],
        ).fetchone()
        if stored is None or json.loads(stored[0]) != json.loads(payload):
            raise ValueError(
                f"catalog identity collision for normalized segment {manifest.source_segment_id}"
            )

    def register_tardis(self, manifest: TardisFileManifest) -> None:
        payload = manifest.canonical_bytes().decode("utf-8")
        key = [manifest.exchange, manifest.data_type, manifest.symbol, manifest.date]
        self.connection.execute(
            """
            INSERT INTO tardis_files VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [*key, manifest.compressed_sha256, payload],
        )
        stored = self.connection.execute(
            """
            SELECT manifest_json FROM tardis_files
            WHERE exchange = ? AND data_type = ? AND symbol = ? AND date = ?
            """,
            key,
        ).fetchone()
        if stored is None or json.loads(stored[0]) != json.loads(payload):
            raise ValueError("catalog identity collision for Tardis file")

    def close(self) -> None:
        if hasattr(self, "connection"):
            self.connection.close()
        self._release_lock()

    def _release_lock(self) -> None:
        if not self._lock.closed:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
