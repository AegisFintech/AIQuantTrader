"""Durable one-way SQLite ingress between the public gateway and isolated engine."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.shadow.models import ShadowIngressEnvelope

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ShadowIngressRecord:
    sequence: int
    envelope: ShadowIngressEnvelope
    envelope_sha256: str


class ShadowIngressWriter:
    """Single gateway writer using full durable commits and no network-side reader API."""

    def __init__(self, path: Path, *, clock_ns: Callable[[], int] = time.time_ns) -> None:
        if not path.is_absolute():
            raise ValueError("shadow ingress path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock_ns = clock_ns
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=DELETE")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS frames (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                receive_ts_ns INTEGER NOT NULL,
                written_ts_ns INTEGER NOT NULL,
                channel TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                envelope_json BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_frames_receive
                ON frames(receive_ts_ns, sequence);
            """
        )
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row[0]) != SCHEMA_VERSION:
            raise ValueError("shadow ingress schema version is unsupported")

    def append(self, frame: ParsedFrame) -> ShadowIngressRecord:
        written = self.clock_ns()
        receive = max(
            (event.header.receive_ts_ns for event in frame.events),
            default=written,
        )
        envelope = ShadowIngressEnvelope(
            channel=frame.channel,
            events=frame.events,
            is_control=frame.is_control,
            receive_ts_ns=receive,
            written_ts_ns=written,
        )
        payload = envelope.canonical_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO frames(
                        receive_ts_ns, written_ts_ns, channel, envelope_sha256, envelope_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (receive, written, frame.channel, digest, payload),
                )
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("shadow ingress insert did not return a sequence")
        sequence = int(cursor.lastrowid)
        return ShadowIngressRecord(sequence, envelope, digest)

    def latest_sequence(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM frames"
            ).fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class ShadowIngressReader:
    """Read-only engine view; digest and contiguous sequence checks fail closed."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("shadow ingress path must be absolute")
        uri = f"{path.as_uri()}?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only=ON")
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or int(row["value"]) != SCHEMA_VERSION:
            raise ValueError("shadow ingress schema version is unsupported")

    def read_after(self, sequence: int, *, limit: int) -> tuple[ShadowIngressRecord, ...]:
        if sequence < 0 or limit < 1:
            raise ValueError("shadow ingress cursor and limit are invalid")
        rows = self._connection.execute(
            """
            SELECT sequence, envelope_sha256, envelope_json FROM frames
            WHERE sequence > ? ORDER BY sequence LIMIT ?
            """,
            (sequence, limit),
        ).fetchall()
        records: list[ShadowIngressRecord] = []
        expected = sequence + 1
        for row in rows:
            observed = int(row["sequence"])
            if observed != expected:
                raise ValueError(
                    f"shadow ingress sequence gap: expected {expected}, observed {observed}"
                )
            payload = bytes(row["envelope_json"])
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row["envelope_sha256"]:
                raise ValueError(f"shadow ingress checksum mismatch at sequence {observed}")
            records.append(
                ShadowIngressRecord(
                    observed,
                    ShadowIngressEnvelope.model_validate_json(payload),
                    digest,
                )
            )
            expected += 1
        return tuple(records)

    def latest_sequence(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM frames"
        ).fetchone()
        return int(row["sequence"])

    def close(self) -> None:
        self._connection.close()
