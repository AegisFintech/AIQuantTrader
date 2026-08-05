"""Immutable Phase 10 legacy-retirement evidence and approval contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class LegacyArchiveArtifactKind(StrEnum):
    FINAL_TRADE_REPORT = "final_trade_report"
    BROKER_ACCOUNT_STATE = "broker_account_state"
    DEPLOYED_RELEASE = "deployed_release"
    RUNTIME_CONFIGURATION = "runtime_configuration"
    COMMON_FILES = "common_files"
    DEAL_ORDER_HISTORY = "deal_order_history"
    STRATEGY_RESEARCH = "strategy_research"
    SERVICE_CONFIGURATION = "service_configuration"
    OPERATIONAL_LOGS = "operational_logs"
    RESTORE_TEST = "restore_test"
    OPERATOR_TIMELINE = "operator_timeline"


REQUIRED_ARCHIVE_ARTIFACTS = frozenset(LegacyArchiveArtifactKind)


class LegacyCapability(StrEnum):
    PM2_MT5 = "pm2_aiquanttrader_mt5"
    PM2_WATCHDOG = "pm2_aiquanttrader_watchdog"
    PM2_REVIEW = "pm2_aiquanttrader_review"
    PM2_DASHBOARD = "pm2_aiquanttrader_dashboard"
    CRON = "aiquanttrader_cron"
    NGINX_ROUTE = "legacy_dashboard_nginx_route"
    LOGROTATE = "legacy_logrotate_policy"
    MT5_AUTOSTART = "mt5_autostart"
    WINE_MT5_PROCESS = "wine_mt5_process"
    COMMAND_FILE_WRITER = "mt5_command_file_writer"


REQUIRED_DISABLED_CAPABILITIES = frozenset(LegacyCapability)


class RequiredNativeDrill(StrEnum):
    NATIVE_ROLLBACK = "native_rollback"
    BACKUP_RESTORE = "backup_restore"
    ALERT_DELIVERY = "alert_delivery"
    OPERATOR_ACCESS = "operator_access"


REQUIRED_NATIVE_DRILLS = frozenset(RequiredNativeDrill)


class LegacyArchiveArtifact(DomainModel):
    kind: LegacyArchiveArtifactKind
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0, le=1_099_511_627_776)
    captured_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def path_is_bounded_to_artifacts(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "artifacts"
        ):
            raise ValueError("legacy archive artifacts must be below artifacts/ without traversal")
        return self


class LegacyArchiveManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    retention_expires_ts_ns: int = Field(gt=0)
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    final_tag_commit_sha: GitCommit
    contains_credentials: Literal[False] = False
    restore_test_passed: Literal[True] = True
    artifacts: tuple[LegacyArchiveArtifact, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )

    @model_validator(mode="after")
    def inventory_and_tag_are_exact(self) -> Self:
        if self.retention_expires_ts_ns <= self.created_ts_ns:
            raise ValueError("legacy archive retention must end after archive creation")
        if self.final_tag_commit_sha != self.source_commit_sha:
            raise ValueError("mt5-final must resolve to the archived source commit")
        kinds = [artifact.kind for artifact in self.artifacts]
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(kinds) != len(set(kinds)) or set(kinds) != REQUIRED_ARCHIVE_ARTIFACTS:
            raise ValueError("legacy archive artifact inventory is incomplete or duplicated")
        if len(paths) != len(set(paths)):
            raise ValueError("legacy archive artifact paths must be unique")
        if any(artifact.captured_ts_ns > self.created_ts_ns for artifact in self.artifacts):
            raise ValueError("legacy archive artifact cannot be captured after manifest creation")
        return self


class NativeProductionObservation(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    admission_id: Sha256
    terminal_authorization_id: Sha256
    renewal_count: int = Field(ge=0)
    authorization_expires_ts_ns: int = Field(gt=0)
    production_approval_sha256: Sha256
    production_artifact_manifest_sha256: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    critical_incidents: int = Field(ge=0)
    reconciliation_failures: int = Field(ge=0)
    risk_breaches: int = Field(ge=0)
    completed_drills: tuple[RequiredNativeDrill, ...] = Field(
        min_length=len(REQUIRED_NATIVE_DRILLS),
        max_length=len(REQUIRED_NATIVE_DRILLS),
    )
    evidence_bundle_sha256: Sha256

    @model_validator(mode="after")
    def interval_and_drills_are_complete(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("native production observation must have a positive interval")
        if self.authorization_expires_ts_ns <= self.ended_ts_ns:
            raise ValueError(
                "native production authorization must remain active through observation"
            )
        if self.renewal_count == 0 and self.terminal_authorization_id != self.admission_id:
            raise ValueError("unrenewed native authorization must equal admission identity")
        if self.renewal_count > 0 and self.terminal_authorization_id == self.admission_id:
            raise ValueError("renewed native authorization must bind the terminal renewal")
        if set(self.completed_drills) != REQUIRED_NATIVE_DRILLS:
            raise ValueError("native production observation must contain every retirement drill")
        return self


class LegacyFinalState(DomainModel):
    schema_version: Literal[1] = 1
    captured_ts_ns: int = Field(ge=0)
    account_mode: Literal["demo"] = "demo"
    instrument_id: Literal["XAUUSD"] = "XAUUSD"
    open_managed_positions: int = Field(ge=0)
    open_unmanaged_positions: int = Field(ge=0)
    pending_orders: int = Field(ge=0)
    entry_pause_active: bool
    command_file_writer_count: int = Field(ge=0)
    final_trade_report_sha256: Sha256
    final_status_sha256: Sha256
    broker_account_state_sha256: Sha256


class RetirementReadinessObservation(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    observed_ts_ns: int = Field(ge=0)
    native: NativeProductionObservation
    archive: LegacyArchiveManifest
    legacy: LegacyFinalState

    @model_validator(mode="after")
    def identities_and_evidence_bind(self) -> Self:
        if self.archive.retirement_id != self.retirement_id:
            raise ValueError("retirement observation and archive identities differ")
        if self.observed_ts_ns < self.native.ended_ts_ns:
            raise ValueError("retirement readiness cannot predate native observation completion")
        if self.observed_ts_ns < self.archive.created_ts_ns:
            raise ValueError("retirement readiness cannot predate archive creation")
        if self.observed_ts_ns < self.legacy.captured_ts_ns:
            raise ValueError("retirement readiness cannot predate final legacy state")
        by_kind = {artifact.kind: artifact for artifact in self.archive.artifacts}
        if (
            by_kind[LegacyArchiveArtifactKind.FINAL_TRADE_REPORT].content_sha256
            != self.legacy.final_trade_report_sha256
        ):
            raise ValueError("final trade report is not bound into the archive")
        if (
            by_kind[LegacyArchiveArtifactKind.BROKER_ACCOUNT_STATE].content_sha256
            != self.legacy.broker_account_state_sha256
        ):
            raise ValueError("broker account state is not bound into the archive")
        return self


class RetirementPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_native_production_observation_ns: int = Field(gt=0)
    minimum_disabled_observation_ns: int = Field(gt=0)
    minimum_archive_retention_ns: int = Field(gt=0)
    required_archive_artifacts: tuple[LegacyArchiveArtifactKind, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )
    required_disabled_capabilities: tuple[LegacyCapability, ...] = Field(
        min_length=len(REQUIRED_DISABLED_CAPABILITIES),
        max_length=len(REQUIRED_DISABLED_CAPABILITIES),
    )
    required_native_drills: tuple[RequiredNativeDrill, ...] = Field(
        min_length=len(REQUIRED_NATIVE_DRILLS),
        max_length=len(REQUIRED_NATIVE_DRILLS),
    )

    @model_validator(mode="after")
    def requirements_are_complete(self) -> Self:
        if set(self.required_archive_artifacts) != REQUIRED_ARCHIVE_ARTIFACTS:
            raise ValueError("retirement policy must require the complete archive inventory")
        if set(self.required_disabled_capabilities) != REQUIRED_DISABLED_CAPABILITIES:
            raise ValueError("retirement policy must require every legacy capability disabled")
        if set(self.required_native_drills) != REQUIRED_NATIVE_DRILLS:
            raise ValueError("retirement policy must require every native recovery drill")
        return self


class RetirementReadinessGate(StrEnum):
    POLICY_FROZEN = "policy_frozen"
    NATIVE_OBSERVATION = "native_observation"
    NATIVE_CLEAN = "native_clean"
    NATIVE_DRILLS = "native_drills"
    ARCHIVE_INVENTORY = "archive_inventory"
    ARCHIVE_RESTORE = "archive_restore"
    ARCHIVE_RETENTION = "archive_retention"
    ARCHIVE_NO_CREDENTIALS = "archive_no_credentials"
    FINAL_TAG = "final_tag"
    DEMO_ACCOUNT = "demo_account"
    ENTRY_PAUSE = "entry_pause"
    FLAT_ACCOUNT = "flat_account"
    NO_COMMAND_WRITERS = "no_command_writers"


class RetirementGateResult(DomainModel):
    gate: RetirementReadinessGate
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=2_048)]
    required: Annotated[str, Field(min_length=1, max_length=2_048)]


class RetirementReadinessReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    retirement_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    observation_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    native_deployment_id: Identifier
    native_admission_id: Sha256
    native_authorization_id: Sha256
    archive_manifest_sha256: Sha256
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    gates: tuple[RetirementGateResult, ...]
    awaiting_stop_approval: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"report_id", "awaiting_stop_approval"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("retirement readiness report identity does not match")
        names = [gate.gate for gate in self.gates]
        if len(names) != len(RetirementReadinessGate) or set(names) != set(RetirementReadinessGate):
            raise ValueError("retirement readiness report must contain every gate exactly once")
        if self.awaiting_stop_approval != all(gate.passed for gate in self.gates):
            raise ValueError("retirement readiness verdict does not match its gates")
        return self


class LegacyCapabilityObservation(DomainModel):
    capability: LegacyCapability
    disabled: bool
    active_instance_count: int = Field(ge=0)
    evidence_sha256: Sha256


class DisabledObservation(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    readiness_report_sha256: Sha256
    stop_approval_sha256: Sha256
    archive_manifest_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    native_critical_incidents: int = Field(ge=0)
    native_reconciliation_failures: int = Field(ge=0)
    native_risk_breaches: int = Field(ge=0)
    legacy_broker_orders_after_stop: int = Field(ge=0)
    archive_reverified: bool
    legacy_credentials_quarantined: bool
    capabilities: tuple[LegacyCapabilityObservation, ...] = Field(
        min_length=len(REQUIRED_DISABLED_CAPABILITIES),
        max_length=len(REQUIRED_DISABLED_CAPABILITIES),
    )
    evidence_bundle_sha256: Sha256

    @model_validator(mode="after")
    def interval_and_capabilities_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("disabled observation must have a positive interval")
        capabilities = [item.capability for item in self.capabilities]
        if (
            len(capabilities) != len(set(capabilities))
            or set(capabilities) != REQUIRED_DISABLED_CAPABILITIES
        ):
            raise ValueError("disabled observation must cover every legacy capability")
        return self


class DisabledObservationGate(StrEnum):
    POLICY_FROZEN = "policy_frozen"
    OBSERVATION_WINDOW = "observation_window"
    ALL_CAPABILITIES_DISABLED = "all_capabilities_disabled"
    ZERO_ACTIVE_INSTANCES = "zero_active_instances"
    NO_LEGACY_ORDERS = "no_legacy_orders"
    NATIVE_STABLE = "native_stable"
    ARCHIVE_REVERIFIED = "archive_reverified"
    CREDENTIALS_QUARANTINED = "credentials_quarantined"


class DisabledGateResult(DomainModel):
    gate: DisabledObservationGate
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=2_048)]
    required: Annotated[str, Field(min_length=1, max_length=2_048)]


class DisabledObservationReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    retirement_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    observation_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    readiness_report_sha256: Sha256
    stop_approval_sha256: Sha256
    archive_manifest_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    gates: tuple[DisabledGateResult, ...]
    awaiting_cleanup_approval: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"report_id", "awaiting_cleanup_approval"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("disabled observation report identity does not match")
        names = [gate.gate for gate in self.gates]
        if len(names) != len(DisabledObservationGate) or set(names) != set(DisabledObservationGate):
            raise ValueError("disabled observation report must contain every gate exactly once")
        if self.awaiting_cleanup_approval != all(gate.passed for gate in self.gates):
            raise ValueError("disabled observation verdict does not match its gates")
        return self


class CleanupTargetKind(StrEnum):
    REPOSITORY_PATH = "repository_path"
    RUNTIME_PATH = "runtime_path"
    HOST_INTEGRATION = "host_integration"
    SECRET_REFERENCE = "secret_reference"
    HOST_PACKAGE = "host_package"


class CleanupAction(StrEnum):
    REMOVE = "remove"
    MIGRATE_NATIVE = "migrate_native"
    REVOKE = "revoke"
    RETAIN_ARCHIVE_ONLY = "retain_archive_only"


class LegacyCleanupTarget(DomainModel):
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    expected_state_sha256: Sha256
    rationale: Annotated[str, Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def locator_is_explicit_and_bounded(self) -> Self:
        forbidden_tokens = (
            "*",
            "?",
            "[",
            "]",
            "\n",
            "\r",
            "$",
            "`",
            ";",
            "|",
            "&",
            "<",
            ">",
            "{",
            "}",
        )
        if any(token in self.locator for token in forbidden_tokens):
            raise ValueError(
                "cleanup target locators cannot contain globs, variables, or shell operators"
            )
        if self.kind in {
            CleanupTargetKind.REPOSITORY_PATH,
            CleanupTargetKind.RUNTIME_PATH,
            CleanupTargetKind.HOST_INTEGRATION,
        }:
            path = PurePosixPath(self.locator)
            if ".." in path.parts:
                raise ValueError("cleanup target paths cannot traverse")
            if self.kind is CleanupTargetKind.REPOSITORY_PATH:
                if path.is_absolute() or str(path) in {"", "."}:
                    raise ValueError("repository cleanup targets must be explicit relative paths")
            else:
                forbidden = {"/", "/root", "/tmp", "/etc", "/usr", "/var"}
                if not path.is_absolute() or str(path) in forbidden or len(path.parts) < 4:
                    raise ValueError("host cleanup targets must be narrow absolute paths")
        elif "/" in self.locator or "\\" in self.locator or " " in self.locator:
            raise ValueError("secret and package locators must be single identifiers")
        if (
            self.kind is CleanupTargetKind.SECRET_REFERENCE
            and self.action is not CleanupAction.REVOKE
        ):
            raise ValueError("secret references must use the revoke action")
        if self.kind is CleanupTargetKind.HOST_PACKAGE and self.action is not CleanupAction.REMOVE:
            raise ValueError("host packages may only use the remove action")
        return self


class LegacyCleanupManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    targets: tuple[LegacyCleanupTarget, ...] = Field(min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def targets_are_unique(self) -> Self:
        ids = [target.target_id for target in self.targets]
        locators = [(target.kind, target.locator) for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("cleanup target identities must be unique")
        if len(locators) != len(set(locators)):
            raise ValueError("cleanup target locators must be unique within each kind")
        return self


class RetirementActionScope(StrEnum):
    STOP_AND_OBSERVE = "stop_and_observe"
    REMOVE_AND_CLEAN = "remove_and_clean"


class RetirementActionApproval(DomainModel):
    schema_version: Literal[1] = 1
    approval_id: Identifier
    retirement_id: Identifier
    scope: RetirementActionScope
    report_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    archive_manifest_sha256: Sha256
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    cleanup_manifest_sha256: Sha256 | None = None
    approver: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def scope_and_window_are_valid(self) -> Self:
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("retirement approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("retirement approval expiry must follow approval time")
        if self.expires_at - self.approved_at > timedelta(hours=24):
            raise ValueError("retirement action approval cannot remain valid over 24 hours")
        if self.scope is RetirementActionScope.STOP_AND_OBSERVE:
            if self.cleanup_manifest_sha256 is not None:
                raise ValueError("stop approval cannot authorize a cleanup manifest")
        elif self.cleanup_manifest_sha256 is None:
            raise ValueError("cleanup approval must bind the exact cleanup manifest")
        return self

    def is_active(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("retirement approval check timestamp must be timezone-aware")
        return self.approved_at <= now < self.expires_at


class RetirementApprovalSignature(DomainModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["ed25519"] = "ed25519"
    key_id: Identifier
    approval_sha256: Sha256
    signature_base64: Annotated[str, Field(min_length=88, max_length=88)]

    @model_validator(mode="after")
    def signature_is_canonical(self) -> Self:
        try:
            decoded = base64.b64decode(self.signature_base64, validate=True)
        except ValueError as exc:
            raise ValueError("retirement signature is not canonical base64") from exc
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != self.signature_base64:
            raise ValueError("retirement signature must be one canonical Ed25519 signature")
        return self

    def signature_bytes(self) -> bytes:
        return base64.b64decode(self.signature_base64, validate=True)


class VerifiedRetirementApproval(DomainModel):
    schema_version: Literal[1] = 1
    verification_id: Sha256
    approval: RetirementActionApproval
    public_key_sha256: Sha256
    signature_envelope_sha256: Sha256
    verified_at: datetime

    @model_validator(mode="after")
    def identity_matches(self) -> Self:
        if self.verified_at.tzinfo is None:
            raise ValueError("retirement verification timestamp must be timezone-aware")
        identity = self.model_dump(mode="json", exclude={"verification_id", "verified_at"})
        if canonical_sha256(identity) != self.verification_id:
            raise ValueError("retirement approval verification identity does not match")
        if not self.approval.is_active(self.verified_at):
            raise ValueError("retirement approval is not active at verification")
        return self
