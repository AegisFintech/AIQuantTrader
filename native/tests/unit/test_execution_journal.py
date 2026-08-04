from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from aiquanttrader_native.domain.execution import ExecutionJournalEvent, ExecutionState
from aiquanttrader_native.execution import (
    DuplicateIntentError,
    ExecutionJournal,
    InvalidTransitionError,
)


def event(
    state: ExecutionState,
    *,
    intent_id: str = "intent-1",
    event_id: str | None = None,
    client_order_id: str | None = None,
) -> ExecutionJournalEvent:
    return ExecutionJournalEvent(
        event_id=event_id or f"event-{state.value}",
        intent_id=intent_id,
        client_order_id=client_order_id,
        state=state,
        event_ts_ns=100,
        filled_quantity_base=Decimal("0"),
        detail="journal test",
        source="nautilus",
    )


def test_journal_enforces_idempotency_and_lifecycle(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    journal.begin(event(ExecutionState.PENDING_SUBMIT))
    with pytest.raises(DuplicateIntentError):
        journal.begin(event(ExecutionState.PENDING_SUBMIT, event_id="duplicate"))
    journal.append(event(ExecutionState.SUBMITTED, client_order_id="cloid-1"))
    assert journal.unresolved_command_count() == 1
    journal.append(event(ExecutionState.ACCEPTED, client_order_id="cloid-1"))
    assert journal.unresolved_command_count() == 0
    journal.append(event(ExecutionState.PARTIALLY_FILLED, client_order_id="cloid-1"))
    journal.append(event(ExecutionState.PENDING_CANCEL, client_order_id="cloid-1"))
    journal.append(event(ExecutionState.CANCELED, client_order_id="cloid-1"))

    assert journal.current("intent-1")["state"] == "canceled"  # type: ignore[index]
    assert journal.by_client_order_id("cloid-1")["intent_id"] == "intent-1"  # type: ignore[index]
    assert len(journal.events("intent-1")) == 6
    with pytest.raises(InvalidTransitionError, match="canceled -> accepted"):
        journal.append(event(ExecutionState.ACCEPTED, event_id="late"))
    journal.close()


def test_journal_rejects_unknown_transitions_and_client_collision(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    with pytest.raises(InvalidTransitionError, match="unknown intent"):
        journal.append(event(ExecutionState.ACCEPTED))
    with pytest.raises(InvalidTransitionError, match="begin"):
        journal.begin(event(ExecutionState.ACCEPTED))

    journal.begin(event(ExecutionState.PENDING_SUBMIT))
    journal.append(event(ExecutionState.SUBMITTED, client_order_id="same"))
    journal.begin(
        event(
            ExecutionState.PENDING_SUBMIT,
            intent_id="intent-2",
            event_id="event-2",
        )
    )
    with pytest.raises(InvalidTransitionError, match="another intent"):
        journal.append(
            event(
                ExecutionState.SUBMITTED,
                intent_id="intent-2",
                event_id="event-3",
                client_order_id="same",
            )
        )


def test_startup_marks_unresolved_submissions_unknown(tmp_path: Path) -> None:
    journal = ExecutionJournal((tmp_path / "journal.db").resolve())
    journal.begin(event(ExecutionState.PENDING_SUBMIT))

    def make_unknown(row: sqlite3.Row) -> ExecutionJournalEvent:
        return event(
            ExecutionState.UNKNOWN,
            intent_id=row["intent_id"],
            event_id="unknown",
        )

    assert journal.mark_stale_submissions_unknown(cutoff_ts_ns=100, event_factory=make_unknown) == 1
    assert journal.current("intent-1")["state"] == "unknown"  # type: ignore[index]
    assert journal.mark_stale_submissions_unknown(cutoff_ts_ns=100, event_factory=make_unknown) == 0


@pytest.mark.parametrize(
    "pending_state",
    [ExecutionState.PENDING_MODIFY, ExecutionState.PENDING_CANCEL],
)
def test_stale_modify_and_cancel_commands_become_unknown(
    tmp_path: Path, pending_state: ExecutionState
) -> None:
    journal = ExecutionJournal((tmp_path / f"{pending_state.value}.db").resolve())
    journal.begin(
        event(
            ExecutionState.PENDING_SUBMIT,
            event_id=f"{pending_state.value}-pending-submit",
            client_order_id="cloid-1",
        )
    )
    journal.append(
        event(
            ExecutionState.SUBMITTED,
            event_id=f"{pending_state.value}-submitted",
            client_order_id="cloid-1",
        )
    )
    journal.append(
        event(
            ExecutionState.ACCEPTED,
            event_id=f"{pending_state.value}-accepted",
            client_order_id="cloid-1",
        )
    )
    journal.append(
        event(
            pending_state,
            event_id=f"{pending_state.value}-pending",
            client_order_id="cloid-1",
        )
    )

    def make_unknown(row: sqlite3.Row) -> ExecutionJournalEvent:
        return event(
            ExecutionState.UNKNOWN,
            intent_id=row["intent_id"],
            event_id=f"{pending_state.value}-unknown",
            client_order_id=row["client_order_id"],
        )

    assert journal.mark_stale_submissions_unknown(cutoff_ts_ns=100, event_factory=make_unknown) == 1
    assert journal.current("intent-1")["state"] == "unknown"  # type: ignore[index]


def test_journal_requires_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        ExecutionJournal(Path("relative.db"))
