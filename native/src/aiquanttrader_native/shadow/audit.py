"""Durable operational evidence for a network-isolated shadow process."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aiquanttrader_native.paper.models import PaperRunManifest
from aiquanttrader_native.shadow.models import ShadowDeterminismReport

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ShadowAuditStatistics:
    cycle_samples: int
    health_samples: int
    availability_fraction: Decimal
    ingress_latency_p99_ms: Decimal
    cycle_latency_p99_ms: Decimal
    completed_drills: tuple[str, ...]
    invalidating_events: tuple[str, ...]


class ShadowAuditJournal:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("shadow audit journal path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                manifest_sha256 TEXT NOT NULL,
                image_identity TEXT NOT NULL,
                started_ts_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cycle_samples (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                source_sequence INTEGER NOT NULL,
                completed_ts_ns INTEGER NOT NULL,
                ingress_latency_ns INTEGER NOT NULL,
                cycle_latency_ns INTEGER NOT NULL,
                feature_sha256 TEXT NOT NULL,
                decisions INTEGER NOT NULL,
                commands INTEGER NOT NULL,
                PRIMARY KEY(run_id, source_sequence)
            );
            CREATE TABLE IF NOT EXISTS health_samples (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sample_ts_ns INTEGER NOT NULL,
                healthy INTEGER NOT NULL,
                ingress_sequence INTEGER NOT NULL,
                ingress_lag_ns INTEGER
            );
            CREATE TABLE IF NOT EXISTS drills (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                drill TEXT NOT NULL,
                completed_ts_ns INTEGER NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                evidence_path TEXT NOT NULL,
                PRIMARY KEY(run_id, drill)
            );
            CREATE TABLE IF NOT EXISTS failures (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                failed_ts_ns INTEGER NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comparisons (
                report_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                report_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_health_run_ts
                ON health_samples(run_id, sample_ts_ns);
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
        elif int(row["value"]) != SCHEMA_VERSION:
            raise ValueError("shadow audit schema version is unsupported")

    def begin_run(self, manifest: PaperRunManifest, image_identity: str) -> bool:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT manifest_sha256, image_identity FROM runs WHERE run_id = ?",
                (manifest.run_id,),
            ).fetchone()
            if row is not None:
                if row["manifest_sha256"] != manifest.sha256():
                    raise ValueError("shadow audit manifest changed across restart")
                if row["image_identity"] != image_identity:
                    raise ValueError("shadow image identity changed across restart")
                return True
            connection.execute(
                """
                INSERT INTO runs(run_id, manifest_sha256, image_identity, started_ts_ns)
                VALUES (?, ?, ?, ?)
                """,
                (manifest.run_id, manifest.sha256(), image_identity, manifest.started_ts_ns),
            )
            return False

    def record_cycle(
        self,
        run_id: str,
        *,
        source_sequence: int,
        completed_ts_ns: int,
        ingress_latency_ns: int,
        cycle_latency_ns: int,
        feature_sha256: str,
        decisions: int,
        commands: int,
    ) -> None:
        if ingress_latency_ns < 0 or cycle_latency_ns < 0:
            raise ValueError("shadow latency samples cannot be negative")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO cycle_samples(
                    run_id, source_sequence, completed_ts_ns, ingress_latency_ns,
                    cycle_latency_ns, feature_sha256, decisions, commands
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source_sequence,
                    completed_ts_ns,
                    ingress_latency_ns,
                    cycle_latency_ns,
                    feature_sha256,
                    decisions,
                    commands,
                ),
            )

    def record_health(
        self,
        run_id: str,
        *,
        sample_ts_ns: int,
        healthy: bool,
        ingress_sequence: int,
        ingress_lag_ns: int | None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO health_samples(
                    run_id, sample_ts_ns, healthy, ingress_sequence, ingress_lag_ns
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, sample_ts_ns, int(healthy), ingress_sequence, ingress_lag_ns),
            )

    def record_drill(
        self,
        run_id: str,
        drill: str,
        *,
        completed_ts_ns: int,
        evidence_path: Path,
    ) -> str:
        resolved = evidence_path.resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise ValueError("shadow drill evidence must be a non-empty regular file")
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO drills(
                    run_id, drill, completed_ts_ns, evidence_sha256, evidence_path
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, drill) DO UPDATE SET
                    completed_ts_ns = excluded.completed_ts_ns,
                    evidence_sha256 = excluded.evidence_sha256,
                    evidence_path = excluded.evidence_path
                """,
                (run_id, drill, completed_ts_ns, digest, str(resolved)),
            )
        return digest

    def record_failure(self, run_id: str, *, failed_ts_ns: int, kind: str, detail: str) -> None:
        if not kind or not detail:
            raise ValueError("shadow failure kind and detail must be non-empty")
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO failures(run_id, failed_ts_ns, kind, detail) VALUES (?, ?, ?, ?)",
                (run_id, failed_ts_ns, kind, detail),
            )

    def record_comparison(self, run_id: str, report: ShadowDeterminismReport) -> None:
        if report.source_run_id != run_id:
            raise ValueError("determinism report source does not match shadow run")
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO comparisons(report_id, run_id, report_json) VALUES (?, ?, ?)",
                (report.report_id, run_id, report.model_dump_json()),
            )

    def latest_comparison(self, run_id: str) -> ShadowDeterminismReport | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT report_json FROM comparisons WHERE run_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return (
            None if row is None else ShadowDeterminismReport.model_validate_json(row["report_json"])
        )

    def statistics(
        self,
        run_id: str,
        *,
        observation_ns: int,
        health_interval_ns: int,
    ) -> ShadowAuditStatistics:
        if observation_ns < 0 or health_interval_ns <= 0:
            raise ValueError("shadow observation and health interval are invalid")
        with self._lock:
            cycles = self._connection.execute(
                """
                SELECT ingress_latency_ns, cycle_latency_ns FROM cycle_samples
                WHERE run_id = ? ORDER BY source_sequence
                """,
                (run_id,),
            ).fetchall()
            health = self._connection.execute(
                "SELECT healthy FROM health_samples WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            drills = self._connection.execute(
                "SELECT drill FROM drills WHERE run_id = ? ORDER BY drill", (run_id,)
            ).fetchall()
            failures = self._connection.execute(
                "SELECT DISTINCT kind FROM failures WHERE run_id = ? ORDER BY kind",
                (run_id,),
            ).fetchall()
        healthy_ns = min(
            observation_ns,
            sum(int(row["healthy"]) for row in health) * health_interval_ns,
        )
        availability = (
            Decimal(healthy_ns) / Decimal(observation_ns) if observation_ns > 0 else Decimal("0")
        )
        ingress = [int(row["ingress_latency_ns"]) for row in cycles]
        cycle = [int(row["cycle_latency_ns"]) for row in cycles]
        return ShadowAuditStatistics(
            cycle_samples=len(cycles),
            health_samples=len(health),
            availability_fraction=availability,
            ingress_latency_p99_ms=Decimal(_percentile_99(ingress)) / Decimal("1000000"),
            cycle_latency_p99_ms=Decimal(_percentile_99(cycle)) / Decimal("1000000"),
            completed_drills=tuple(str(row["drill"]) for row in drills),
            invalidating_events=tuple(str(row["kind"]) for row in failures),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()


def _percentile_99(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.99) - 1)
    return ordered[index]
