"""Crash-detecting, serialized operational evidence logs."""

from __future__ import annotations

import os
import stat
import uuid
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path

from aiquanttrader_native.acceptance.models import (
    AcceptanceComponent,
    OperationalEventKind,
    OperationalEvidenceEvent,
)
from aiquanttrader_native.domain.execution import RiskReason, RiskState

MAX_AUDIT_BYTES = 67_108_864
MAX_AUDIT_EVENTS = 1_000_000


class OperationalEvidenceLog:
    """Append canonical hash-linked events to one component-owned file."""

    def __init__(self, path: Path, *, component: AcceptanceComponent) -> None:
        if not path.is_absolute():
            raise ValueError("operational evidence path must be absolute")
        self.path = path
        self.component = component
        read_operational_events(path, expected_component=component)

    def append(
        self,
        *,
        kind: OperationalEventKind,
        event_ts_ns: int,
        success: bool,
        detail: str,
        order_count: int | None = None,
        risk_state: RiskState | None = None,
        risk_reasons: tuple[RiskReason, ...] = (),
    ) -> OperationalEvidenceEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.path, flags, 0o600), "a+b", buffering=0) as handle:
            flock(handle.fileno(), LOCK_EX)
            try:
                metadata = os.fstat(handle.fileno())
                payload = os.pread(handle.fileno(), metadata.st_size, 0)
                events = _parse_operational_payload(
                    payload,
                    metadata=metadata,
                    expected_component=self.component,
                )
                event = OperationalEvidenceEvent(
                    event_id=f"{self.component.value}-{uuid.uuid4()}",
                    sequence=len(events) + 1,
                    prior_event_sha256=None if not events else events[-1].sha256(),
                    component=self.component,
                    kind=kind,
                    event_ts_ns=event_ts_ns,
                    success=success,
                    order_count=order_count,
                    risk_state=risk_state,
                    risk_reasons=risk_reasons,
                    detail=detail,
                )
                record = event.canonical_bytes() + b"\n"
                if metadata.st_size + len(record) > MAX_AUDIT_BYTES:
                    raise ValueError("operational evidence log exceeds its hard size bound")
                handle.write(record)
                os.fsync(handle.fileno())
            finally:
                flock(handle.fileno(), LOCK_UN)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return event


def read_operational_events(
    path: Path,
    *,
    expected_component: AcceptanceComponent | None = None,
) -> tuple[OperationalEvidenceEvent, ...]:
    if not path.exists():
        return ()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("cannot open operational evidence log") from exc
    try:
        metadata = os.fstat(descriptor)
        payload = os.pread(descriptor, metadata.st_size, 0)
        final = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(metadata, field) != getattr(final, field) for field in identity):
        raise ValueError("operational evidence log changed while read")
    return _parse_operational_payload(
        payload,
        metadata=metadata,
        expected_component=expected_component,
    )


def _parse_operational_payload(
    payload: bytes,
    *,
    metadata: os.stat_result,
    expected_component: AcceptanceComponent | None,
) -> tuple[OperationalEvidenceEvent, ...]:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("operational evidence log must be a regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("operational evidence log cannot be group/world writable")
    if metadata.st_size > MAX_AUDIT_BYTES or len(payload) != metadata.st_size:
        raise ValueError("operational evidence log exceeds its hard size bound or changed on read")
    if payload and not payload.endswith(b"\n"):
        raise ValueError("operational evidence log has an incomplete final record")
    lines = payload.splitlines()
    if len(lines) > MAX_AUDIT_EVENTS:
        raise ValueError("operational evidence log exceeds its hard event bound")
    events: list[OperationalEvidenceEvent] = []
    prior_sha256: str | None = None
    for sequence, line in enumerate(lines, start=1):
        event = OperationalEvidenceEvent.model_validate_json(line)
        if event.canonical_bytes() != line:
            raise ValueError("operational evidence event is not canonical JSON")
        if event.sequence != sequence or event.prior_event_sha256 != prior_sha256:
            raise ValueError("operational evidence hash chain is broken")
        if expected_component is not None and event.component is not expected_component:
            raise ValueError("operational evidence component does not match its owner")
        events.append(event)
        prior_sha256 = event.sha256()
    return tuple(events)
