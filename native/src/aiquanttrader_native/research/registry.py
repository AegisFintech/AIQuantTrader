"""Single-writer DuckDB registry for immutable research and stage history."""

from __future__ import annotations

import fcntl
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.domain.governance import (
    ActorKind,
    PromotionStage,
    validate_stage_transition,
)
from aiquanttrader_native.research.models import (
    ChampionChallengerReport,
    ResearchExperimentManifest,
)

ArtifactKind = Literal["dataset", "feature_schema", "model", "report", "deployment"]
ARTIFACT_KINDS = frozenset({"dataset", "feature_schema", "model", "report", "deployment"})


class ResearchRegistry:
    """Own one writable registry process/thread and preserve append-only identities."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path.resolve()
        self._owner_pid = os.getpid()
        self._owner_thread = threading.get_ident()
        self._lock_path = self._path.with_suffix(self._path.suffix + ".writer.lock")
        self._lock = self._lock_path.open("a+b")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            raise RuntimeError("research registry already has a writer") from exc
        self._connection = duckdb.connect(str(self._path))
        self._closed = False
        self._initialize()

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                kind VARCHAR NOT NULL,
                artifact_id VARCHAR NOT NULL,
                payload_sha256 VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (kind, artifact_id)
            );
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id VARCHAR PRIMARY KEY,
                payload_sha256 VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                initial_stage VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stage_events (
                event_id VARCHAR PRIMARY KEY,
                experiment_id VARCHAR NOT NULL,
                previous_stage VARCHAR NOT NULL,
                target_stage VARCHAR NOT NULL,
                actor_kind VARCHAR NOT NULL,
                actor_id VARCHAR NOT NULL,
                evidence_sha256 VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL
            );
            """
        )

    def register_artifact(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        payload: DomainModel,
        *,
        created_at: datetime,
    ) -> None:
        self._require_owner()
        if kind not in ARTIFACT_KINDS or not artifact_id:
            raise ValueError("registry artifact kind and identity must be valid")
        self._require_aware(created_at)
        content = payload.canonical_bytes().decode("utf-8")
        digest = payload.sha256()
        existing = self._connection.execute(
            "SELECT payload_sha256, payload_json FROM artifacts WHERE kind = ? AND artifact_id = ?",
            [kind, artifact_id],
        ).fetchone()
        if existing is not None:
            if existing != (digest, content):
                raise ValueError("artifact identity already exists with different content")
            return
        self._connection.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
            [kind, artifact_id, digest, content, created_at],
        )

    def register_experiment(self, experiment: ResearchExperimentManifest) -> None:
        self._require_owner()
        if experiment.stage is not PromotionStage.DRAFT:
            raise ValueError("new research experiments must enter the registry at draft stage")
        content = experiment.canonical_bytes().decode("utf-8")
        digest = experiment.sha256()
        existing = self._connection.execute(
            "SELECT payload_sha256, payload_json FROM experiments WHERE experiment_id = ?",
            [experiment.experiment_id],
        ).fetchone()
        if existing is not None:
            if existing != (digest, content):
                raise ValueError("experiment identity already exists with different content")
            return
        self._connection.execute(
            "INSERT INTO experiments VALUES (?, ?, ?, ?, ?)",
            [
                experiment.experiment_id,
                digest,
                content,
                experiment.stage.value,
                experiment.created_at,
            ],
        )

    def current_stage(self, experiment_id: str) -> PromotionStage:
        self._require_owner()
        row = self._connection.execute(
            """
            SELECT COALESCE(
                (SELECT target_stage FROM stage_events
                 WHERE experiment_id = ? ORDER BY occurred_at DESC, event_id DESC LIMIT 1),
                (SELECT initial_stage FROM experiments WHERE experiment_id = ?)
            )
            """,
            [experiment_id, experiment_id],
        ).fetchone()
        if row is None or row[0] is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        return PromotionStage(row[0])

    def advance_experiment(
        self,
        *,
        experiment_id: str,
        target: PromotionStage,
        actor: ActorKind,
        actor_id: str,
        evidence_sha256: str,
        occurred_at: datetime,
    ) -> str:
        self._require_owner()
        if not actor_id or occurred_at.tzinfo is None:
            raise ValueError("stage events require actor identity and aware time")
        if len(evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in evidence_sha256):
            raise ValueError("stage evidence must be a lowercase SHA-256")
        previous = self.current_stage(experiment_id)
        validate_stage_transition(previous, target, actor)
        if target in {PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION}:
            raise ValueError("Phase 9 signed deployment approval is not implemented")
        if target is PromotionStage.AWAITING_APPROVAL:
            self._require_passing_report(experiment_id, evidence_sha256)
        latest_time = self._connection.execute(
            """
            SELECT COALESCE(
                (SELECT max(occurred_at) FROM stage_events WHERE experiment_id = ?),
                (SELECT created_at FROM experiments WHERE experiment_id = ?)
            )
            """,
            [experiment_id, experiment_id],
        ).fetchone()
        if latest_time is None or latest_time[0] is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        if occurred_at <= latest_time[0]:
            raise ValueError("stage event time must increase monotonically")
        payload = {
            "experiment_id": experiment_id,
            "previous_stage": previous,
            "target_stage": target,
            "actor_kind": actor,
            "actor_id": actor_id,
            "evidence_sha256": evidence_sha256,
            "occurred_at": occurred_at.isoformat(),
        }
        event_id = canonical_sha256(payload)
        self._connection.execute(
            "INSERT INTO stage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                event_id,
                experiment_id,
                previous.value,
                target.value,
                actor.value,
                actor_id,
                evidence_sha256,
                occurred_at,
            ],
        )
        return event_id

    def experiment_count(self) -> int:
        self._require_owner()
        row = self._connection.execute("SELECT count(*) FROM experiments").fetchone()
        if row is None:
            raise RuntimeError("research registry count query returned no row")
        return int(row[0])

    def _require_passing_report(self, experiment_id: str, evidence_sha256: str) -> None:
        experiment_row = self._connection.execute(
            "SELECT payload_json FROM experiments WHERE experiment_id = ?",
            [experiment_id],
        ).fetchone()
        if experiment_row is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        experiment = ResearchExperimentManifest.model_validate_json(experiment_row[0])
        if experiment.report_sha256 != evidence_sha256:
            raise ValueError("approval-boundary evidence must match the experiment report hash")
        report_row = self._connection.execute(
            """
            SELECT payload_json FROM artifacts
            WHERE kind = 'report' AND payload_sha256 = ?
            LIMIT 1
            """,
            [evidence_sha256],
        ).fetchone()
        if report_row is None:
            raise ValueError("approval-boundary promotion report is not registered")
        report = ChampionChallengerReport.model_validate_json(report_row[0])
        if report.challenger_experiment_id != experiment_id:
            raise ValueError("promotion report challenger does not match experiment")
        if report.challenger_metrics_sha256 != experiment.metrics.sha256():
            raise ValueError("promotion report metrics do not match experiment")
        if report.negative_controls_sha256 != experiment.negative_controls.sha256():
            raise ValueError("promotion report controls do not match experiment")
        if not report.passed:
            raise ValueError("failed promotion report cannot reach awaiting approval")

    def close(self) -> None:
        if self._closed:
            return
        self._require_owner()
        self._connection.close()
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        self._lock.close()
        self._closed = True

    def _require_owner(self) -> None:
        if self._closed:
            raise RuntimeError("research registry is closed")
        if os.getpid() != self._owner_pid or threading.get_ident() != self._owner_thread:
            raise RuntimeError("research registry writes require the owning process and thread")

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("registry timestamps must be timezone-aware")

    def __enter__(self) -> ResearchRegistry:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
