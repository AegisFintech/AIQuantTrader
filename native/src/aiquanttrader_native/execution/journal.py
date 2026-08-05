"""Durable local execution journal and idempotency authority."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from aiquanttrader_native.domain.execution import ExecutionJournalEvent, ExecutionState


class DuplicateIntentError(ValueError):
    """Raised when an intent ID has already entered the execution lifecycle."""


class InvalidTransitionError(ValueError):
    """Raised when an event would corrupt the order lifecycle."""


TERMINAL_STATES = {
    ExecutionState.FILLED,
    ExecutionState.CANCELED,
    ExecutionState.REJECTED,
    ExecutionState.DENIED,
}

ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PENDING_SUBMIT: frozenset(
        {ExecutionState.SUBMITTED, ExecutionState.DENIED, ExecutionState.UNKNOWN}
    ),
    ExecutionState.SUBMITTED: frozenset(
        {
            ExecutionState.ACCEPTED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.DENIED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.ACCEPTED: frozenset(
        {
            ExecutionState.PENDING_MODIFY,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.PENDING_CANCEL,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.PARTIALLY_FILLED: frozenset(
        {
            ExecutionState.PENDING_MODIFY,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.PENDING_CANCEL,
            ExecutionState.CANCELED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.PENDING_CANCEL: frozenset(
        {
            ExecutionState.CANCELED,
            ExecutionState.FILLED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.ACCEPTED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.PENDING_MODIFY: frozenset(
        {
            ExecutionState.ACCEPTED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        }
    ),
    ExecutionState.UNKNOWN: frozenset(
        {
            ExecutionState.ACCEPTED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.PENDING_CANCEL,
            ExecutionState.CANCELED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
            ExecutionState.PENDING_MODIFY,
        }
    ),
}


class ExecutionJournal:
    """SQLite WAL journal; one process owns writes and every transition is transactional."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("execution journal path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        with self._transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    intent_id TEXT PRIMARY KEY,
                    client_order_id TEXT UNIQUE,
                    venue_order_id TEXT,
                    state TEXT NOT NULL,
                    updated_ts_ns INTEGER NOT NULL,
                    filled_quantity_base TEXT NOT NULL
                ) STRICT
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    intent_id TEXT NOT NULL,
                    event_ts_ns INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(intent_id) REFERENCES orders(intent_id)
                ) STRICT
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_intent ON events(intent_id, sequence)"
            )

    def begin(self, event: ExecutionJournalEvent) -> None:
        if event.state is not ExecutionState.PENDING_SUBMIT:
            raise InvalidTransitionError("new intents must begin in pending_submit")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM orders WHERE intent_id = ?", (event.intent_id,)
            ).fetchone()
            if existing is not None:
                raise DuplicateIntentError(f"intent already journaled: {event.intent_id}")
            connection.execute(
                """
                INSERT INTO orders(
                    intent_id, client_order_id, venue_order_id, state,
                    updated_ts_ns, filled_quantity_base
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.intent_id,
                    event.client_order_id,
                    event.venue_order_id,
                    event.state.value,
                    event.event_ts_ns,
                    str(event.filled_quantity_base),
                ),
            )
            self._insert_event(connection, event)

    def append(self, event: ExecutionJournalEvent) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT state FROM orders WHERE intent_id = ?", (event.intent_id,)
            ).fetchone()
            if row is None:
                raise InvalidTransitionError(f"unknown intent: {event.intent_id}")
            current = ExecutionState(row["state"])
            allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
            if event.state is not current and event.state not in allowed:
                raise InvalidTransitionError(
                    f"invalid execution transition {current.value} -> {event.state.value}"
                )
            try:
                connection.execute(
                    """
                    UPDATE orders
                    SET client_order_id = COALESCE(?, client_order_id),
                        venue_order_id = COALESCE(?, venue_order_id),
                        state = ?, updated_ts_ns = ?, filled_quantity_base = ?
                    WHERE intent_id = ?
                    """,
                    (
                        event.client_order_id,
                        event.venue_order_id,
                        event.state.value,
                        event.event_ts_ns,
                        str(event.filled_quantity_base),
                        event.intent_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise InvalidTransitionError("client order ID is bound to another intent") from exc
            self._insert_event(connection, event)

    def current(self, intent_id: str) -> sqlite3.Row | None:
        with self._lock:
            return cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT * FROM orders WHERE intent_id = ?", (intent_id,)
                ).fetchone(),
            )

    def by_client_order_id(self, client_order_id: str) -> sqlite3.Row | None:
        with self._lock:
            return cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
                ).fetchone(),
            )

    def unresolved_command_count(self) -> int:
        """Count commands whose authoritative exchange outcome is not yet known."""

        states = (
            ExecutionState.PENDING_SUBMIT.value,
            ExecutionState.SUBMITTED.value,
            ExecutionState.PENDING_MODIFY.value,
            ExecutionState.PENDING_CANCEL.value,
            ExecutionState.UNKNOWN.value,
        )
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE state IN (?, ?, ?, ?, ?)",
                states,
            ).fetchone()
        return int(row["count"])

    def unknown_command_count(self) -> int:
        """Count commands whose state still requires explicit reconciliation."""

        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM orders WHERE state = ?",
                (ExecutionState.UNKNOWN.value,),
            ).fetchone()
        return int(row["count"])

    def mark_stale_submissions_unknown(
        self,
        *,
        cutoff_ts_ns: int,
        event_factory: Callable[[sqlite3.Row], ExecutionJournalEvent],
    ) -> int:
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT intent_id, client_order_id, venue_order_id, filled_quantity_base
                FROM orders
                WHERE state IN (?, ?, ?, ?) AND updated_ts_ns <= ?
                """,
                (
                    ExecutionState.PENDING_SUBMIT.value,
                    ExecutionState.SUBMITTED.value,
                    ExecutionState.PENDING_MODIFY.value,
                    ExecutionState.PENDING_CANCEL.value,
                    cutoff_ts_ns,
                ),
            ).fetchall()
            for row in rows:
                event = event_factory(row)
                connection.execute(
                    """
                    UPDATE orders
                    SET state = ?, updated_ts_ns = ?
                    WHERE intent_id = ?
                    """,
                    (ExecutionState.UNKNOWN.value, event.event_ts_ns, event.intent_id),
                )
                self._insert_event(connection, event)
            return len(rows)

    def events(self, intent_id: str) -> tuple[ExecutionJournalEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM events WHERE intent_id = ? ORDER BY sequence",
                (intent_id,),
            ).fetchall()
        return tuple(ExecutionJournalEvent.model_validate_json(row["event_json"]) for row in rows)

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
    def _insert_event(connection: sqlite3.Connection, event: ExecutionJournalEvent) -> None:
        connection.execute(
            """
            INSERT INTO events(event_id, intent_id, event_ts_ns, state, event_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.intent_id,
                event.event_ts_ns,
                event.state.value,
                event.model_dump_json(),
            ),
        )
