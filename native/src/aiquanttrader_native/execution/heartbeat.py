"""Crash-safe heartbeat shared with the independent safety sentinel."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from aiquanttrader_native.domain.execution import TradingHeartbeat
from aiquanttrader_native.governance.models import VerifiedDeploymentAdmission
from aiquanttrader_native.risk.kill_switch import KillSwitchStore


class HeartbeatPublisher:
    def __init__(
        self,
        path: Path,
        *,
        environment: str,
        account_address: str,
        config_fingerprint: str,
        kill_switch: KillSwitchStore,
        admission: VerifiedDeploymentAdmission | None = None,
    ) -> None:
        if not path.is_absolute():
            raise ValueError("heartbeat path must be absolute")
        self._path = path
        self._environment = environment
        self._account_address = account_address
        self._config_fingerprint = config_fingerprint
        self._kill_switch = kill_switch
        self._admission = admission
        self._execution_healthy = False
        self._reconciliation_complete = False
        self._healthy_until_ns: int | None = None
        self._lock = threading.Lock()

    def set_health(
        self,
        *,
        execution_healthy: bool,
        reconciliation_complete: bool,
        valid_for_ms: int | None = None,
    ) -> None:
        with self._lock:
            self._execution_healthy = execution_healthy
            self._reconciliation_complete = reconciliation_complete
            self._healthy_until_ns = (
                None if valid_for_ms is None else time.time_ns() + valid_for_ms * 1_000_000
            )

    def publish(self, *, now_ns: int | None = None) -> TradingHeartbeat:
        with self._lock:
            healthy = self._execution_healthy
            reconciled = self._reconciliation_complete
            healthy_until_ns = self._healthy_until_ns
        kill_active = self._kill_switch.read().active
        timestamp_ns = time.time_ns() if now_ns is None else now_ns
        if healthy_until_ns is not None and timestamp_ns > healthy_until_ns:
            healthy = False
        heartbeat = TradingHeartbeat(
            process_id=os.getpid(),
            heartbeat_ts_ns=timestamp_ns,
            environment=self._environment,
            account_address=self._account_address,
            execution_healthy=healthy and not kill_active,
            reconciliation_complete=reconciled,
            operator_kill=kill_active,
            config_fingerprint=self._config_fingerprint,
            deployment_id=(
                None if self._admission is None else self._admission.approval.deployment_id
            ),
            approval_id=(None if self._admission is None else self._admission.approval.approval_id),
            admission_id=None if self._admission is None else self._admission.admission_id,
            approval_expires_ts_ns=(
                None
                if self._admission is None
                else int(self._admission.approval.expires_at.timestamp() * 1_000_000_000)
            ),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(heartbeat.canonical_bytes() + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return heartbeat


def read_heartbeat(path: Path) -> TradingHeartbeat:
    return TradingHeartbeat.model_validate_json(path.read_bytes())
