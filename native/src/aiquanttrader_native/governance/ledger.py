"""Durable anti-replay authority for explicitly admitted deployments."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from aiquanttrader_native.domain.governance import PromotionStage
from aiquanttrader_native.governance.models import (
    DeploymentAdmissionRecord,
    DeploymentAdmissionState,
    VerifiedDeploymentAdmission,
)

SCHEMA_VERSION = 1


class DeploymentAdmissionLedger:
    """One-writer deployment registry; read-only guards may run in trading processes."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        if not path.is_absolute():
            raise ValueError("deployment admission ledger path must be absolute")
        self.path = path
        self.read_only = read_only
        if read_only:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = threading.RLock()
        if read_only:
            self._verify_schema()
        else:
            self._initialize()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admissions (
                deployment_id TEXT PRIMARY KEY,
                approval_id TEXT NOT NULL UNIQUE,
                admission_id TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL,
                account_address TEXT NOT NULL,
                vault_address TEXT,
                state TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transitions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                deployment_id TEXT NOT NULL REFERENCES admissions(deployment_id),
                occurred_at TEXT NOT NULL,
                previous_state TEXT,
                next_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_admission
                ON admissions(account_address, COALESCE(vault_address, ''), state)
                WHERE state = 'active';
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
            raise ValueError("deployment admission ledger schema is unsupported")

    def _verify_schema(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or int(row["value"]) != SCHEMA_VERSION:
            raise ValueError("deployment admission ledger schema is unsupported")

    def admit(
        self,
        admission: VerifiedDeploymentAdmission,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> DeploymentAdmissionRecord:
        self._require_writer()
        instant = datetime.now(UTC) if now is None else now
        _validate_transition_input(actor, reason, instant)
        approval = admission.approval
        if not approval.is_active(instant):
            raise ValueError("cannot admit an inactive deployment approval")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT record_json FROM admissions WHERE approval_id = ? OR deployment_id = ?",
                (approval.approval_id, approval.deployment_id),
            ).fetchone()
            if existing is not None:
                record = DeploymentAdmissionRecord.model_validate_json(existing["record_json"])
                if (
                    record.admission_id == admission.admission_id
                    and record.state is DeploymentAdmissionState.ACTIVE
                ):
                    return record
                raise ValueError("deployment or approval identity has already been consumed")

            active = self._active_row(connection, approval.account_address, approval.vault_address)
            if approval.stage is PromotionStage.APPROVED_CANARY:
                if active is not None:
                    raise ValueError("an active deployment already owns this mainnet account")
            else:
                if active is None:
                    raise ValueError("production admission requires its active canary predecessor")
                predecessor = DeploymentAdmissionRecord.model_validate_json(active["record_json"])
                if (
                    predecessor.stage is not PromotionStage.APPROVED_CANARY
                    or predecessor.approval_id != approval.prior_approval_id
                    or predecessor.deployment_id != approval.rollback_deployment_id
                ):
                    raise ValueError("production admission does not bind the active canary")
                superseded = predecessor.model_copy(
                    update={
                        "state": DeploymentAdmissionState.SUPERSEDED,
                        "actor": actor,
                        "reason": f"superseded by {approval.deployment_id}: {reason}",
                    }
                )
                self._update_record(connection, superseded)
                self._insert_transition(
                    connection,
                    superseded,
                    previous=DeploymentAdmissionState.ACTIVE,
                    actor=actor,
                    reason=superseded.reason,
                    occurred_at=instant,
                )

            record = DeploymentAdmissionRecord(
                deployment_id=approval.deployment_id,
                approval_id=approval.approval_id,
                admission_id=admission.admission_id,
                stage=approval.stage,
                account_address=approval.account_address,
                vault_address=approval.vault_address,
                artifact_manifest_sha256=approval.artifact_manifest_sha256,
                configuration_sha256=approval.configuration_sha256,
                image_digest=approval.image_digest,
                capital_limit_usd=approval.capital_limit_usd,
                admitted_at=instant,
                expires_at=approval.expires_at,
                state=DeploymentAdmissionState.ACTIVE,
                actor=actor,
                reason=reason,
            )
            connection.execute(
                """
                INSERT INTO admissions(
                    deployment_id, approval_id, admission_id, stage, account_address,
                    vault_address, state, expires_at, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.deployment_id,
                    record.approval_id,
                    record.admission_id,
                    record.stage.value,
                    record.account_address.lower(),
                    None if record.vault_address is None else record.vault_address.lower(),
                    record.state.value,
                    record.expires_at.isoformat(),
                    record.model_dump_json(),
                ),
            )
            self._insert_transition(
                connection,
                record,
                previous=None,
                actor=actor,
                reason=reason,
                occurred_at=instant,
            )
        return record

    def deactivate(
        self,
        deployment_id: str,
        *,
        target: DeploymentAdmissionState,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> DeploymentAdmissionRecord:
        self._require_writer()
        if target not in {DeploymentAdmissionState.ROLLED_BACK, DeploymentAdmissionState.REVOKED}:
            raise ValueError("operator may only roll back or revoke an admission")
        instant = datetime.now(UTC) if now is None else now
        _validate_transition_input(actor, reason, instant)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT record_json FROM admissions WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise ValueError("deployment admission is not registered")
            current = DeploymentAdmissionRecord.model_validate_json(row["record_json"])
            if current.state is not DeploymentAdmissionState.ACTIVE:
                raise ValueError("only an active deployment admission can be deactivated")
            updated = current.model_copy(update={"state": target, "actor": actor, "reason": reason})
            self._update_record(connection, updated)
            self._insert_transition(
                connection,
                updated,
                previous=current.state,
                actor=actor,
                reason=reason,
                occurred_at=instant,
            )
        return updated

    def require_active(
        self,
        admission: VerifiedDeploymentAdmission,
        *,
        now: datetime | None = None,
    ) -> DeploymentAdmissionRecord:
        instant = datetime.now(UTC) if now is None else now
        if instant.tzinfo is None:
            raise ValueError("deployment admission timestamp must be timezone-aware")
        with self._lock:
            row = self._connection.execute(
                "SELECT record_json FROM admissions WHERE deployment_id = ?",
                (admission.approval.deployment_id,),
            ).fetchone()
        if row is None:
            raise ValueError("deployment has not been explicitly admitted")
        record = DeploymentAdmissionRecord.model_validate_json(row["record_json"])
        if (
            record.state is not DeploymentAdmissionState.ACTIVE
            or record.admission_id != admission.admission_id
            or record.approval_id != admission.approval.approval_id
            or not admission.approval.is_active(instant)
            or instant >= record.expires_at
        ):
            raise ValueError("deployment admission is inactive, expired, or mismatched")
        return record

    def active(self) -> DeploymentAdmissionRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT record_json FROM admissions WHERE state = 'active' LIMIT 1"
            ).fetchone()
        return (
            None
            if row is None
            else DeploymentAdmissionRecord.model_validate_json(row["record_json"])
        )

    def get(self, deployment_id: str) -> DeploymentAdmissionRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT record_json FROM admissions WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
        return (
            None
            if row is None
            else DeploymentAdmissionRecord.model_validate_json(row["record_json"])
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _require_writer(self) -> None:
        if self.read_only:
            raise ValueError("read-only deployment ledger cannot change admission state")

    @staticmethod
    def _active_row(
        connection: sqlite3.Connection,
        account_address: str,
        vault_address: str | None,
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """
                SELECT record_json FROM admissions
                WHERE account_address = ? AND COALESCE(vault_address, '') = ? AND state = 'active'
                """,
                (
                    account_address.lower(),
                    "" if vault_address is None else vault_address.lower(),
                ),
            ).fetchone(),
        )

    @staticmethod
    def _update_record(connection: sqlite3.Connection, record: DeploymentAdmissionRecord) -> None:
        connection.execute(
            "UPDATE admissions SET state = ?, record_json = ? WHERE deployment_id = ?",
            (record.state.value, record.model_dump_json(), record.deployment_id),
        )

    @staticmethod
    def _insert_transition(
        connection: sqlite3.Connection,
        record: DeploymentAdmissionRecord,
        *,
        previous: DeploymentAdmissionState | None,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO transitions(
                deployment_id, occurred_at, previous_state, next_state, actor, reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.deployment_id,
                occurred_at.isoformat(),
                None if previous is None else previous.value,
                record.state.value,
                actor,
                reason,
            ),
        )

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


class DeploymentAdmissionGuard:
    """Re-validate durable admission and expiry on every exposure-changing command."""

    def __init__(
        self,
        ledger: DeploymentAdmissionLedger,
        admission: VerifiedDeploymentAdmission,
    ) -> None:
        self.ledger = ledger
        self.admission = admission

    @property
    def capital_limit_usd(self) -> Decimal:
        return self.admission.approval.capital_limit_usd

    def require_active(self, *, now: datetime | None = None) -> DeploymentAdmissionRecord:
        return self.ledger.require_active(self.admission, now=now)

    def is_active(self, *, now: datetime | None = None) -> bool:
        try:
            self.require_active(now=now)
        except ValueError:
            return False
        return True


def _validate_transition_input(actor: str, reason: str, instant: datetime) -> None:
    if instant.tzinfo is None:
        raise ValueError("deployment transition timestamp must be timezone-aware")
    if not actor.strip() or len(actor) > 256:
        raise ValueError("deployment transition actor is invalid")
    if not reason.strip() or len(reason) > 512:
        raise ValueError("deployment transition reason is invalid")
