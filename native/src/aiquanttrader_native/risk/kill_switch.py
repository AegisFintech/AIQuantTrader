"""Crash-safe persistent operator kill state."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from aiquanttrader_native.domain.base import DomainModel


class KillSwitchRecord(DomainModel):
    schema_version: Literal[1] = 1
    record_id: Annotated[str, Field(min_length=1, max_length=128)]
    active: bool
    changed_ts_ns: Annotated[int, Field(ge=0)]
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    reason: Annotated[str, Field(min_length=1, max_length=512)]


class KillSwitchStore:
    """Persist the current kill state and append every change to an audit log."""

    def __init__(self, state_path: Path, *, clock_ns: Callable[[], int] = time.time_ns) -> None:
        if not state_path.is_absolute():
            raise ValueError("kill-switch state path must be absolute")
        self._state_path = state_path
        self._audit_path = state_path.with_suffix(state_path.suffix + ".audit.jsonl")
        self._clock_ns = clock_ns

    def read(self) -> KillSwitchRecord:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return KillSwitchRecord.model_validate(payload)
        except FileNotFoundError:
            return KillSwitchRecord(
                record_id="initial-inactive",
                active=False,
                changed_ts_ns=0,
                actor="system",
                reason="no operator kill has been activated",
            )
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            return KillSwitchRecord(
                record_id="corrupt-fail-closed",
                active=True,
                changed_ts_ns=self._clock_ns(),
                actor="system",
                reason=f"kill-switch state unreadable: {type(exc).__name__}",
            )

    def activate(self, *, actor: str, reason: str) -> KillSwitchRecord:
        return self._write(active=True, actor=actor, reason=reason)

    def clear(self, *, actor: str, reason: str) -> KillSwitchRecord:
        return self._write(active=False, actor=actor, reason=reason)

    def _write(self, *, active: bool, actor: str, reason: str) -> KillSwitchRecord:
        record = KillSwitchRecord(
            record_id=str(uuid.uuid4()),
            active=active,
            changed_ts_ns=self._clock_ns(),
            actor=actor,
            reason=reason,
        )
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.canonical_bytes() + b"\n"
        temporary = self._state_path.with_name(f".{self._state_path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if active:
                temporary.replace(self._state_path)
                self._fsync_directory()
                self._append_audit(payload)
            else:
                # A crash during clear must leave the previously active state in force.
                self._append_audit(payload)
                temporary.replace(self._state_path)
                self._fsync_directory()
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def _append_audit(self, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        audit_fd = os.open(self._audit_path, flags, 0o600)
        with os.fdopen(audit_fd, "ab") as audit:
            audit.write(payload)
            audit.flush()
            os.fsync(audit.fileno())
        self._fsync_directory()

    def _fsync_directory(self) -> None:
        descriptor = os.open(self._state_path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
