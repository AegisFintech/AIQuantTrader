"""Transactional SQLite evidence journal and restart authority for paper trading."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from aiquanttrader.backtest.kernel import StrategyAction
from aiquanttrader.features.market_structure import CausalStructureState
from aiquanttrader.features.models import MicrostructureSnapshot, VolatilityRegime
from aiquanttrader.paper.llm_models import LlmConfirmation
from aiquanttrader.paper.models import (
    TERMINAL_PAPER_ORDER_STATES,
    PaperAccountState,
    PaperDecisionRecord,
    PaperEngineCheckpoint,
    PaperExecutionCommand,
    PaperFill,
    PaperMarkout,
    PaperOrder,
    PaperRunManifest,
    PaperStrategyActionCount,
    PaperStrategyEvaluation,
    PaperStrategyEvaluationSummary,
)
from aiquanttrader.research.models import DriftReport

SCHEMA_VERSION = 1
INVALIDATING_EVENT_KINDS = ("funding_gap", "replay_exclusions", "service_failure")


@dataclass(frozen=True, slots=True)
class PaperJournalStatistics:
    started_ts_ns: int
    ended_ts_ns: int
    independent_decisions: int
    approved_decisions: int
    denied_decisions: int
    fills: int
    markouts: int
    ending_position_base: Decimal
    open_orders: int
    regimes: tuple[VolatilityRegime, ...]
    ending_equity_usd: Decimal
    starting_equity_usd: Decimal
    maximum_drawdown_fraction: Decimal
    mean_signed_markout_bps: Decimal
    drift_evaluated: bool
    maximum_feature_psi: Decimal
    maximum_standardized_mean_shift: Decimal
    completed_drills: tuple[str, ...]
    invalidating_events: tuple[str, ...]
    commands: int = 0
    submit_commands: int = 0
    feature_samples: int = 0


class PaperJournal:
    """One-writer journal; all state required for restart is committed atomically."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("paper journal path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
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
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_ts_ns INTEGER NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                paper_order_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                intent_id TEXT NOT NULL,
                state TEXT NOT NULL,
                updated_ts_ns INTEGER NOT NULL,
                order_json TEXT NOT NULL,
                UNIQUE(run_id, intent_id)
            );
            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                fill_ts_ns INTEGER NOT NULL,
                fill_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                record_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                decision_ts_ns INTEGER NOT NULL,
                allowed INTEGER NOT NULL,
                independent INTEGER NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sequence INTEGER NOT NULL,
                evaluated_ts_ns INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                feature_ready INTEGER NOT NULL,
                structure_ready INTEGER NOT NULL,
                feed_connected INTEGER NOT NULL,
                evaluation_json TEXT NOT NULL,
                UNIQUE(run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sequence INTEGER NOT NULL,
                command_ts_ns INTEGER NOT NULL,
                kind TEXT NOT NULL,
                intent_id TEXT NOT NULL,
                source_sequence INTEGER,
                command_json TEXT NOT NULL,
                UNIQUE(run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS account_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                snapshot_ts_ns INTEGER NOT NULL,
                equity_usd TEXT NOT NULL,
                high_water_equity_usd TEXT NOT NULL,
                account_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS features (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                observed_ts_ns INTEGER NOT NULL,
                regime TEXT NOT NULL,
                ready INTEGER NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                vector_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS markouts (
                fill_id TEXT NOT NULL REFERENCES fills(fill_id),
                horizon_ns INTEGER NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                markout_json TEXT NOT NULL,
                PRIMARY KEY(fill_id, horizon_ns)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                checkpoint_ts_ns INTEGER NOT NULL,
                checkpoint_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS market_structure_checkpoints (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                revision INTEGER NOT NULL,
                observed_ts_ns INTEGER NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_confirmations (
                confirmation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                request_id TEXT NOT NULL,
                completed_ts_ns INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                confirmation_json TEXT NOT NULL,
                UNIQUE(run_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS drift_reports (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                observed_ts_ns INTEGER NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drills (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                drill TEXT NOT NULL,
                completed_ts_ns INTEGER NOT NULL,
                evidence TEXT NOT NULL,
                PRIMARY KEY(run_id, drill)
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_ts_ns INTEGER NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_run_state ON orders(run_id, state);
            CREATE INDEX IF NOT EXISTS idx_decisions_run_ts ON decisions(run_id, decision_ts_ns);
            CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_run_ts
                ON strategy_evaluations(run_id, evaluated_ts_ns);
            CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_run_gate
                ON strategy_evaluations(run_id, action, reason);
            CREATE INDEX IF NOT EXISTS idx_commands_run_ts ON commands(run_id, command_ts_ns);
            CREATE INDEX IF NOT EXISTS idx_accounts_run_ts
                ON account_snapshots(run_id, snapshot_ts_ns);
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
            raise ValueError("paper journal schema version is unsupported")

    def begin_run(self, manifest: PaperRunManifest, account: PaperAccountState) -> bool:
        """Create a run or validate exact identity when resuming. Returns True on resume."""

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT manifest_sha256, manifest_json FROM runs WHERE run_id = ?",
                (manifest.run_id,),
            ).fetchone()
            if existing is not None:
                if existing["manifest_sha256"] != manifest.sha256():
                    raise ValueError("paper run identity changed across restart")
                return True
            connection.execute(
                """
                INSERT INTO runs(run_id, started_ts_ns, manifest_sha256, manifest_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    manifest.run_id,
                    manifest.started_ts_ns,
                    manifest.sha256(),
                    manifest.model_dump_json(),
                ),
            )
            self._insert_account(connection, manifest.run_id, account)
            return False

    def latest_manifest(self) -> PaperRunManifest | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT manifest_json FROM runs ORDER BY started_ts_ns DESC LIMIT 1"
            ).fetchone()
        return None if row is None else PaperRunManifest.model_validate_json(row["manifest_json"])

    def latest_account(self, run_id: str) -> PaperAccountState | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT account_json FROM account_snapshots
                WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return None if row is None else PaperAccountState.model_validate_json(row["account_json"])

    def restore_open_orders(self, run_id: str) -> tuple[PaperOrder, ...]:
        terminal = tuple(state.value for state in TERMINAL_PAPER_ORDER_STATES)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT order_json FROM orders
                WHERE run_id = ? AND state NOT IN (?, ?, ?)
                ORDER BY updated_ts_ns, paper_order_id
                """,
                (run_id, *terminal),
            ).fetchall()
        return tuple(PaperOrder.model_validate_json(row["order_json"]) for row in rows)

    def record_cycle(
        self,
        run_id: str,
        *,
        orders: Sequence[PaperOrder],
        fills: Sequence[PaperFill],
        account: PaperAccountState,
        commands: Sequence[PaperExecutionCommand] = (),
    ) -> None:
        with self._transaction() as connection:
            self._insert_commands(connection, run_id, commands)
            for order in orders:
                connection.execute(
                    """
                    INSERT INTO orders(
                        paper_order_id, run_id, intent_id, state, updated_ts_ns, order_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(paper_order_id) DO UPDATE SET
                        state = excluded.state,
                        updated_ts_ns = excluded.updated_ts_ns,
                        order_json = excluded.order_json
                    """,
                    (
                        order.paper_order_id,
                        run_id,
                        order.intent.intent_id,
                        order.state.value,
                        order.updated_ts_ns,
                        order.model_dump_json(),
                    ),
                )
            for fill in fills:
                connection.execute(
                    """
                    INSERT INTO fills(fill_id, run_id, fill_ts_ns, fill_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (fill.fill_id, run_id, fill.fill_ts_ns, fill.model_dump_json()),
                )
            self._insert_account(connection, run_id, account)

    def record_engine_cycle(
        self,
        run_id: str,
        *,
        orders: Sequence[PaperOrder],
        fills: Sequence[PaperFill],
        account: PaperAccountState,
        feature: MicrostructureSnapshot,
        decisions: Sequence[PaperDecisionRecord],
        markouts: Sequence[PaperMarkout],
        checkpoint: PaperEngineCheckpoint,
        drift_report: DriftReport | None,
        strategy_evaluation: PaperStrategyEvaluation,
        commands: Sequence[PaperExecutionCommand] = (),
    ) -> None:
        """Commit one causal market-state transition as a single durability unit."""

        with self._transaction() as connection:
            self._insert_commands(connection, run_id, commands)
            for order in orders:
                connection.execute(
                    """
                    INSERT INTO orders(
                        paper_order_id, run_id, intent_id, state, updated_ts_ns, order_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(paper_order_id) DO UPDATE SET
                        state = excluded.state,
                        updated_ts_ns = excluded.updated_ts_ns,
                        order_json = excluded.order_json
                    """,
                    (
                        order.paper_order_id,
                        run_id,
                        order.intent.intent_id,
                        order.state.value,
                        order.updated_ts_ns,
                        order.model_dump_json(),
                    ),
                )
            for fill in fills:
                connection.execute(
                    "INSERT INTO fills(fill_id, run_id, fill_ts_ns, fill_json) VALUES (?, ?, ?, ?)",
                    (fill.fill_id, run_id, fill.fill_ts_ns, fill.model_dump_json()),
                )
            for record in decisions:
                connection.execute(
                    """
                    INSERT INTO decisions(
                        record_id, run_id, decision_ts_ns, allowed, independent, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.record_id,
                        run_id,
                        record.decision_ts_ns,
                        int(record.risk_decision.allowed),
                        int(record.independent),
                        record.model_dump_json(),
                    ),
                )
            connection.execute(
                """
                INSERT INTO strategy_evaluations(
                    evaluation_id, run_id, sequence, evaluated_ts_ns, action, reason,
                    feature_ready, structure_ready, feed_connected, evaluation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_evaluation.evaluation_id,
                    run_id,
                    strategy_evaluation.sequence,
                    strategy_evaluation.evaluated_ts_ns,
                    strategy_evaluation.decision.action.value,
                    strategy_evaluation.decision.reason,
                    int(strategy_evaluation.feature_ready),
                    int(strategy_evaluation.structure_ready),
                    int(strategy_evaluation.feed_connected),
                    strategy_evaluation.model_dump_json(),
                ),
            )
            connection.execute(
                """
                INSERT INTO features(
                    run_id, observed_ts_ns, regime, ready, snapshot_sha256, vector_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    feature.receive_ts_ns,
                    feature.volatility_regime.value,
                    int(feature.ready),
                    feature.sha256(),
                    json.dumps(feature.model_vector().tolist(), separators=(",", ":")),
                ),
            )
            for markout in markouts:
                connection.execute(
                    """
                    INSERT INTO markouts(fill_id, horizon_ns, run_id, markout_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (markout.fill_id, markout.horizon_ns, run_id, markout.model_dump_json()),
                )
            self._insert_account(connection, run_id, account)
            connection.execute(
                """
                INSERT INTO checkpoints(run_id, checkpoint_ts_ns, checkpoint_json)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    checkpoint_ts_ns = excluded.checkpoint_ts_ns,
                    checkpoint_json = excluded.checkpoint_json
                """,
                (
                    checkpoint.run_id,
                    checkpoint.checkpoint_ts_ns,
                    checkpoint.model_dump_json(),
                ),
            )
            if drift_report is not None:
                connection.execute(
                    """
                    INSERT INTO drift_reports(run_id, observed_ts_ns, report_json)
                    VALUES (?, ?, ?)
                    """,
                    (run_id, feature.receive_ts_ns, drift_report.model_dump_json()),
                )

    def record_decision(self, run_id: str, record: PaperDecisionRecord) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO decisions(
                    record_id, run_id, decision_ts_ns, allowed, independent, record_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    run_id,
                    record.decision_ts_ns,
                    int(record.risk_decision.allowed),
                    int(record.independent),
                    record.model_dump_json(),
                ),
            )

    def record_feature(self, run_id: str, snapshot: MicrostructureSnapshot) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO features(
                    run_id, observed_ts_ns, regime, ready, snapshot_sha256, vector_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    snapshot.receive_ts_ns,
                    snapshot.volatility_regime.value,
                    int(snapshot.ready),
                    snapshot.sha256(),
                    json.dumps(snapshot.model_vector().tolist(), separators=(",", ":")),
                ),
            )

    def feature_vectors(
        self, run_id: str, *, baseline_samples: int, current_samples: int
    ) -> tuple[tuple[float, ...], ...]:
        if baseline_samples < 1 or current_samples < 1:
            raise ValueError("paper drift restore windows must be positive")
        with self._lock:
            count = int(
                self._connection.execute(
                    "SELECT COUNT(*) AS count FROM features WHERE run_id = ? AND ready = 1",
                    (run_id,),
                ).fetchone()["count"]
            )
            if count <= baseline_samples + current_samples:
                rows = self._connection.execute(
                    """
                    SELECT vector_json FROM features
                    WHERE run_id = ? AND ready = 1 ORDER BY sequence
                    """,
                    (run_id,),
                ).fetchall()
            else:
                first = self._connection.execute(
                    """
                    SELECT vector_json FROM features
                    WHERE run_id = ? AND ready = 1 ORDER BY sequence LIMIT ?
                    """,
                    (run_id, baseline_samples),
                ).fetchall()
                last = self._connection.execute(
                    """
                    SELECT vector_json FROM features
                    WHERE run_id = ? AND ready = 1 ORDER BY sequence DESC LIMIT ?
                    """,
                    (run_id, current_samples),
                ).fetchall()
                rows = [*first, *reversed(last)]
        return tuple(
            tuple(float(value) for value in json.loads(row["vector_json"])) for row in rows
        )

    def record_markout(self, run_id: str, markout: PaperMarkout) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO markouts(fill_id, horizon_ns, run_id, markout_json)
                VALUES (?, ?, ?, ?)
                """,
                (markout.fill_id, markout.horizon_ns, run_id, markout.model_dump_json()),
            )

    def record_checkpoint(self, checkpoint: PaperEngineCheckpoint) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(run_id, checkpoint_ts_ns, checkpoint_json)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    checkpoint_ts_ns = excluded.checkpoint_ts_ns,
                    checkpoint_json = excluded.checkpoint_json
                """,
                (
                    checkpoint.run_id,
                    checkpoint.checkpoint_ts_ns,
                    checkpoint.model_dump_json(),
                ),
            )

    def latest_checkpoint(self, run_id: str) -> PaperEngineCheckpoint | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT checkpoint_json FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        return (
            None
            if row is None
            else PaperEngineCheckpoint.model_validate_json(row["checkpoint_json"])
        )

    def record_market_structure_state(
        self,
        run_id: str,
        state: CausalStructureState,
    ) -> None:
        if state.last_observed_ts_ns is None:
            return
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO market_structure_checkpoints(
                    run_id, revision, observed_ts_ns, state_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    revision = excluded.revision,
                    observed_ts_ns = excluded.observed_ts_ns,
                    state_json = excluded.state_json
                """,
                (
                    run_id,
                    state.revision,
                    state.last_observed_ts_ns,
                    state.model_dump_json(),
                ),
            )

    def latest_market_structure_state(self, run_id: str) -> CausalStructureState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM market_structure_checkpoints WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else CausalStructureState.model_validate_json(row["state_json"])

    def record_llm_confirmation(self, confirmation: LlmConfirmation) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO llm_confirmations(
                    confirmation_id, run_id, request_id, completed_ts_ns,
                    verdict, confirmation_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    confirmation.confirmation_id,
                    confirmation.run_id,
                    confirmation.request_id,
                    confirmation.completed_ts_ns,
                    confirmation.assessment.verdict.value,
                    confirmation.model_dump_json(),
                ),
            )

    def latest_llm_confirmation(self, run_id: str) -> LlmConfirmation | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT confirmation_json FROM llm_confirmations
                WHERE run_id = ? ORDER BY completed_ts_ns DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return (
            None if row is None else LlmConfirmation.model_validate_json(row["confirmation_json"])
        )

    def next_command_sequence(self, run_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 AS next FROM commands WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["next"])

    def commands(self, run_id: str) -> tuple[PaperExecutionCommand, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT command_json FROM commands WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(PaperExecutionCommand.model_validate_json(row["command_json"]) for row in rows)

    def decisions(self, run_id: str) -> tuple[PaperDecisionRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT record_json FROM decisions WHERE run_id = ? ORDER BY decision_ts_ns, rowid",
                (run_id,),
            ).fetchall()
        return tuple(PaperDecisionRecord.model_validate_json(row["record_json"]) for row in rows)

    def next_strategy_evaluation_sequence(self, run_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(sequence), -1) + 1 AS next
                FROM strategy_evaluations WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return int(row["next"])

    def strategy_evaluations(self, run_id: str) -> tuple[PaperStrategyEvaluation, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT evaluation_json FROM strategy_evaluations
                WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            PaperStrategyEvaluation.model_validate_json(row["evaluation_json"]) for row in rows
        )

    def strategy_evaluation_summary(self, run_id: str) -> PaperStrategyEvaluationSummary:
        with self._lock:
            if (
                self._connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                is None
            ):
                raise ValueError(f"unknown paper run: {run_id}")
            totals = self._connection.execute(
                """
                SELECT COUNT(*) AS evaluations,
                    COALESCE(SUM(feature_ready), 0) AS feature_ready,
                    COALESCE(SUM(structure_ready), 0) AS structure_ready,
                    COALESCE(SUM(feed_connected), 0) AS feed_connected,
                    MIN(evaluated_ts_ns) AS first_ts,
                    MAX(evaluated_ts_ns) AS last_ts
                FROM strategy_evaluations WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            gates = self._connection.execute(
                """
                SELECT action, reason, COUNT(*) AS count
                FROM strategy_evaluations WHERE run_id = ?
                GROUP BY action, reason
                ORDER BY count DESC, action, reason
                """,
                (run_id,),
            ).fetchall()
            latest = self._connection.execute(
                """
                SELECT evaluation_json FROM strategy_evaluations
                WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        latest_evaluation = (
            None
            if latest is None
            else PaperStrategyEvaluation.model_validate_json(latest["evaluation_json"])
        )
        return PaperStrategyEvaluationSummary(
            run_id=run_id,
            evaluations=int(totals["evaluations"]),
            feature_ready_evaluations=int(totals["feature_ready"]),
            structure_ready_evaluations=int(totals["structure_ready"]),
            feed_connected_evaluations=int(totals["feed_connected"]),
            first_evaluated_ts_ns=(None if totals["first_ts"] is None else int(totals["first_ts"])),
            last_evaluated_ts_ns=(None if totals["last_ts"] is None else int(totals["last_ts"])),
            action_counts=tuple(
                PaperStrategyActionCount(
                    action=StrategyAction(str(row["action"])),
                    reason=str(row["reason"]),
                    count=int(row["count"]),
                )
                for row in gates
            ),
            latest_forecast=(None if latest_evaluation is None else latest_evaluation.forecast),
        )

    def pending_markout_fills(self, run_id: str, horizon_ns: int) -> tuple[PaperFill, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT f.fill_json FROM fills AS f
                LEFT JOIN markouts AS m
                    ON m.fill_id = f.fill_id AND m.horizon_ns = ?
                WHERE f.run_id = ? AND m.fill_id IS NULL
                ORDER BY f.fill_ts_ns, f.fill_id
                """,
                (horizon_ns, run_id),
            ).fetchall()
        return tuple(PaperFill.model_validate_json(row["fill_json"]) for row in rows)

    def record_drill(self, run_id: str, drill: str, *, ts_ns: int, evidence: str) -> None:
        if not drill or not evidence:
            raise ValueError("paper drill and evidence must be non-empty")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO drills(run_id, drill, completed_ts_ns, evidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, drill) DO UPDATE SET
                    completed_ts_ns = excluded.completed_ts_ns,
                    evidence = excluded.evidence
                """,
                (run_id, drill, ts_ns, evidence),
            )

    def record_event(self, run_id: str, *, ts_ns: int, kind: str, detail: str) -> None:
        if not kind or not detail:
            raise ValueError("paper event kind and detail must be non-empty")
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO events(run_id, event_ts_ns, kind, detail) VALUES (?, ?, ?, ?)",
                (run_id, ts_ns, kind, detail),
            )

    def statistics(self, run_id: str) -> PaperJournalStatistics:
        with self._lock:
            manifest_row = self._connection.execute(
                "SELECT started_ts_ns FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if manifest_row is None:
                raise ValueError(f"unknown paper run: {run_id}")
            decision = self._connection.execute(
                """
                SELECT
                    COALESCE(SUM(independent), 0) AS independent_count,
                    COALESCE(SUM(allowed), 0) AS approved_count,
                    COALESCE(SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END), 0) AS denied_count
                FROM decisions WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            fill_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) AS count FROM fills WHERE run_id = ?", (run_id,)
                ).fetchone()["count"]
            )
            terminal = tuple(state.value for state in TERMINAL_PAPER_ORDER_STATES)
            open_order_count = int(
                self._connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM orders
                    WHERE run_id = ? AND state NOT IN (?, ?, ?)
                    """,
                    (run_id, *terminal),
                ).fetchone()["count"]
            )
            account_rows = self._connection.execute(
                """
                SELECT snapshot_ts_ns, equity_usd, high_water_equity_usd, account_json
                FROM account_snapshots WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
            regimes = self._connection.execute(
                """
                SELECT DISTINCT regime FROM features
                WHERE run_id = ? AND ready = 1 AND regime != 'warmup' ORDER BY regime
                """,
                (run_id,),
            ).fetchall()
            markout_rows = self._connection.execute(
                "SELECT markout_json FROM markouts WHERE run_id = ?", (run_id,)
            ).fetchall()
            drills = self._connection.execute(
                "SELECT drill FROM drills WHERE run_id = ? ORDER BY drill", (run_id,)
            ).fetchall()
            drift_rows = self._connection.execute(
                """
                SELECT report_json FROM drift_reports
                WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
            invalidating_events = self._connection.execute(
                """
                SELECT DISTINCT kind FROM events
                WHERE run_id = ? AND kind IN (?, ?, ?) ORDER BY kind
                """,
                (run_id, *INVALIDATING_EVENT_KINDS),
            ).fetchall()
            command_counts = self._connection.execute(
                """
                SELECT COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN kind = 'submit' THEN 1 ELSE 0 END), 0) AS submits
                FROM commands WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            feature_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) AS count FROM features WHERE run_id = ?", (run_id,)
                ).fetchone()["count"]
            )
        if not account_rows:
            raise ValueError("paper run has no account snapshots")
        equities = [Decimal(row["equity_usd"]) for row in account_rows]
        ending_account = PaperAccountState.model_validate_json(account_rows[-1]["account_json"])
        high_waters = [Decimal(row["high_water_equity_usd"]) for row in account_rows]
        drawdowns = [
            max(Decimal("0"), (high - equity) / high)
            for equity, high in zip(equities, high_waters, strict=True)
            if high > 0
        ]
        markouts = [
            PaperMarkout.model_validate_json(row["markout_json"]).signed_markout_bps
            for row in markout_rows
        ]
        drift_reports = [DriftReport.model_validate_json(row["report_json"]) for row in drift_rows]
        return PaperJournalStatistics(
            started_ts_ns=int(manifest_row["started_ts_ns"]),
            ended_ts_ns=int(account_rows[-1]["snapshot_ts_ns"]),
            independent_decisions=int(decision["independent_count"]),
            approved_decisions=int(decision["approved_count"]),
            denied_decisions=int(decision["denied_count"]),
            fills=fill_count,
            markouts=len(markouts),
            ending_position_base=ending_account.position_base,
            open_orders=open_order_count,
            regimes=tuple(VolatilityRegime(row["regime"]) for row in regimes),
            ending_equity_usd=equities[-1],
            starting_equity_usd=equities[0],
            maximum_drawdown_fraction=max(drawdowns, default=Decimal("0")),
            mean_signed_markout_bps=(
                sum(markouts, Decimal("0")) / len(markouts) if markouts else Decimal("0")
            ),
            drift_evaluated=bool(drift_reports),
            maximum_feature_psi=(
                max(
                    (Decimal(str(report.maximum_psi)) for report in drift_reports),
                    default=Decimal("0"),
                )
            ),
            maximum_standardized_mean_shift=(
                max(
                    (
                        Decimal(str(report.maximum_standardized_mean_shift))
                        for report in drift_reports
                    ),
                    default=Decimal("0"),
                )
            ),
            completed_drills=tuple(str(row["drill"]) for row in drills),
            invalidating_events=tuple(str(row["kind"]) for row in invalidating_events),
            commands=int(command_counts["total"]),
            submit_commands=int(command_counts["submits"]),
            feature_samples=feature_count,
        )

    def fill(self, fill_id: str) -> PaperFill | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT fill_json FROM fills WHERE fill_id = ?", (fill_id,)
            ).fetchone()
        return None if row is None else PaperFill.model_validate_json(row["fill_json"])

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

    @staticmethod
    def _insert_account(
        connection: sqlite3.Connection, run_id: str, account: PaperAccountState
    ) -> None:
        connection.execute(
            """
            INSERT INTO account_snapshots(
                run_id, snapshot_ts_ns, equity_usd, high_water_equity_usd, account_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                account.updated_ts_ns,
                str(account.equity_usd),
                str(account.high_water_equity_usd),
                account.model_dump_json(),
            ),
        )

    @staticmethod
    def _insert_commands(
        connection: sqlite3.Connection,
        run_id: str,
        commands: Sequence[PaperExecutionCommand],
    ) -> None:
        for command in commands:
            connection.execute(
                """
                INSERT INTO commands(
                    command_id, run_id, sequence, command_ts_ns, kind, intent_id,
                    source_sequence, command_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    run_id,
                    command.sequence,
                    command.command_ts_ns,
                    command.kind.value,
                    command.intent_id,
                    command.source_sequence,
                    command.model_dump_json(),
                ),
            )
