"""Durable anti-replay authority for explicitly admitted deployments."""

from __future__ import annotations

import json
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
    VerifiedDeploymentRenewal,
)

SCHEMA_VERSION = 2


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
                authorization_id TEXT NOT NULL UNIQUE,
                renewal_count INTEGER NOT NULL,
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
            CREATE TABLE IF NOT EXISTS renewals (
                authorization_id TEXT PRIMARY KEY,
                renewal_id TEXT NOT NULL UNIQUE,
                deployment_id TEXT NOT NULL REFERENCES admissions(deployment_id),
                prior_authorization_id TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                renewal_json TEXT NOT NULL
            );
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
        elif int(row["value"]) == 1:
            self._migrate_v1()
        elif int(row["value"]) != SCHEMA_VERSION:
            raise ValueError("deployment admission ledger schema is unsupported")
        self._connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_admission
            ON admissions(account_address, COALESCE(vault_address, ''), state)
            WHERE state = 'active'
            """
        )

    def _migrate_v1(self) -> None:
        """Add renewable authorization identity without changing existing authority."""

        with self._transaction() as connection:
            connection.execute("ALTER TABLE admissions ADD COLUMN authorization_id TEXT")
            connection.execute(
                "ALTER TABLE admissions ADD COLUMN renewal_count INTEGER NOT NULL DEFAULT 0"
            )
            rows = connection.execute(
                "SELECT deployment_id, admission_id, record_json FROM admissions"
            ).fetchall()
            for row in rows:
                payload = json.loads(str(row["record_json"]))
                payload["schema_version"] = 2
                payload["authorization_id"] = str(row["admission_id"])
                payload["renewal_count"] = 0
                payload["approval_public_key_sha256"] = None
                record = DeploymentAdmissionRecord.model_validate(payload)
                connection.execute(
                    """
                    UPDATE admissions
                    SET authorization_id = ?, renewal_count = ?, record_json = ?
                    WHERE deployment_id = ?
                    """,
                    (
                        record.authorization_id,
                        record.renewal_count,
                        record.model_dump_json(),
                        record.deployment_id,
                    ),
                )
            connection.execute(
                "CREATE UNIQUE INDEX idx_admission_authorization ON admissions(authorization_id)"
            )
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )

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
                authorization_id=admission.admission_id,
                renewal_count=0,
                approval_public_key_sha256=admission.public_key_sha256,
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
                    deployment_id, approval_id, admission_id, authorization_id,
                    renewal_count, stage, account_address, vault_address, state,
                    expires_at, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.deployment_id,
                    record.approval_id,
                    record.admission_id,
                    record.authorization_id,
                    record.renewal_count,
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

    def renew(
        self,
        renewal: VerifiedDeploymentRenewal,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> DeploymentAdmissionRecord:
        """Transactionally extend an unchanged, still-active production admission."""

        self._require_writer()
        instant = datetime.now(UTC) if now is None else now
        _validate_transition_input(actor, reason, instant)
        authority = renewal.renewal
        if not authority.is_active(instant):
            raise ValueError("cannot apply an inactive deployment renewal")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT record_json FROM admissions WHERE deployment_id = ?",
                (authority.deployment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("deployment admission is not registered")
            current = DeploymentAdmissionRecord.model_validate_json(row["record_json"])
            if current.state is not DeploymentAdmissionState.ACTIVE:
                raise ValueError("only an active deployment admission can be renewed")
            if current.stage is not PromotionStage.PRODUCTION:
                raise ValueError("only production admissions can be renewed")
            if instant >= current.expires_at:
                raise ValueError("expired deployment admission cannot be renewed")
            if authority.admission_id != current.admission_id:
                raise ValueError("deployment renewal admission identity changed")
            if authority.initial_approval_id != current.approval_id:
                raise ValueError("deployment renewal initial approval changed")
            if authority.prior_authorization_id != current.authorization_id:
                raise ValueError("deployment renewal authorization chain is stale")
            immutable_values: tuple[tuple[str, object, object], ...] = (
                (
                    "account_address",
                    authority.account_address.lower(),
                    current.account_address.lower(),
                ),
                (
                    "vault_address",
                    None if authority.vault_address is None else authority.vault_address.lower(),
                    None if current.vault_address is None else current.vault_address.lower(),
                ),
                (
                    "artifact_manifest_sha256",
                    authority.artifact_manifest_sha256,
                    current.artifact_manifest_sha256,
                ),
                (
                    "approval_public_key_sha256",
                    renewal.public_key_sha256,
                    current.approval_public_key_sha256,
                ),
                (
                    "configuration_sha256",
                    authority.configuration_sha256,
                    current.configuration_sha256,
                ),
                ("image_digest", authority.image_digest, current.image_digest),
                ("capital_limit_usd", authority.capital_limit_usd, current.capital_limit_usd),
            )
            for field, actual, expected in immutable_values:
                if actual != expected:
                    raise ValueError(f"deployment renewal changed immutable field: {field}")
            if authority.expires_at <= current.expires_at:
                raise ValueError("deployment renewal does not extend expiry")
            if authority.approved_at < current.admitted_at:
                raise ValueError("deployment renewal predates admission")
            if (
                connection.execute(
                    "SELECT 1 FROM renewals WHERE authorization_id = ?",
                    (renewal.authorization_id,),
                ).fetchone()
                is not None
            ):
                raise ValueError("deployment renewal authorization has already been consumed")

            updated = current.model_copy(
                update={
                    "authorization_id": renewal.authorization_id,
                    "renewal_count": current.renewal_count + 1,
                    "expires_at": authority.expires_at,
                    "actor": actor,
                    "reason": reason,
                }
            )
            connection.execute(
                """
                INSERT INTO renewals(
                    authorization_id, renewal_id, deployment_id, prior_authorization_id,
                    approved_at, expires_at, renewal_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    renewal.authorization_id,
                    authority.renewal_id,
                    current.deployment_id,
                    authority.prior_authorization_id,
                    authority.approved_at.isoformat(),
                    authority.expires_at.isoformat(),
                    renewal.model_dump_json(),
                ),
            )
            self._update_record(connection, updated)
            self._insert_transition(
                connection,
                updated,
                previous=current.state,
                actor=actor,
                reason=f"authorization renewed: {reason}",
                occurred_at=instant,
            )
        return updated

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

    def renewal_history(self, deployment_id: str) -> tuple[VerifiedDeploymentRenewal, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT renewal_json FROM renewals
                WHERE deployment_id = ? ORDER BY approved_at, authorization_id
                """,
                (deployment_id,),
            ).fetchall()
        return tuple(
            VerifiedDeploymentRenewal.model_validate_json(row["renewal_json"]) for row in rows
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
            """
            UPDATE admissions
            SET state = ?, authorization_id = ?, renewal_count = ?,
                expires_at = ?, record_json = ?
            WHERE deployment_id = ?
            """,
            (
                record.state.value,
                record.authorization_id,
                record.renewal_count,
                record.expires_at.isoformat(),
                record.model_dump_json(),
                record.deployment_id,
            ),
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

    @property
    def expires_at(self) -> datetime:
        try:
            record = self.ledger.get(self.admission.approval.deployment_id)
        except (sqlite3.Error, ValueError):
            record = None
        return self.admission.approval.expires_at if record is None else record.expires_at

    def require_active(self, *, now: datetime | None = None) -> DeploymentAdmissionRecord:
        return self.ledger.require_active(self.admission, now=now)

    def is_active(self, *, now: datetime | None = None) -> bool:
        return self.active_record(now=now) is not None

    def active_record(self, *, now: datetime | None = None) -> DeploymentAdmissionRecord | None:
        try:
            return self.require_active(now=now)
        except (sqlite3.Error, ValueError):
            return None


def _validate_transition_input(actor: str, reason: str, instant: datetime) -> None:
    if instant.tzinfo is None:
        raise ValueError("deployment transition timestamp must be timezone-aware")
    if not actor.strip() or len(actor) > 256:
        raise ValueError("deployment transition actor is invalid")
    if not reason.strip() or len(reason) > 512:
        raise ValueError("deployment transition reason is invalid")
