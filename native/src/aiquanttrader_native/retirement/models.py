"""Immutable Phase 10 legacy-retirement evidence and approval contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.governance.models import DeploymentArtifactKind

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


class ProductionEvidenceCategory(StrEnum):
    ADMISSION_LEDGER = "admission_ledger"
    DEPLOYMENT_APPROVAL = "deployment_approval"
    APPROVAL_SIGNATURE = "approval_signature"
    APPROVAL_PUBLIC_KEY = "approval_public_key"
    ARTIFACT_MANIFEST = "artifact_manifest"
    RELEASE_ARTIFACT = "release_artifact"
    AUTHORIZATION_RENEWAL = "authorization_renewal"
    AUTHORIZATION_RENEWAL_SIGNATURE = "authorization_renewal_signature"
    EXECUTION_AUDIT = "execution_audit"
    SENTINEL_AUDIT = "sentinel_audit"
    INCIDENT_REGISTER = "incident_register"
    DRILL_REPORT = "drill_report"
    SUPPORTING_EVIDENCE = "supporting_evidence"


PRODUCTION_EVIDENCE_SINGLETONS = frozenset(
    {
        ProductionEvidenceCategory.ADMISSION_LEDGER,
        ProductionEvidenceCategory.DEPLOYMENT_APPROVAL,
        ProductionEvidenceCategory.APPROVAL_SIGNATURE,
        ProductionEvidenceCategory.APPROVAL_PUBLIC_KEY,
        ProductionEvidenceCategory.ARTIFACT_MANIFEST,
        ProductionEvidenceCategory.EXECUTION_AUDIT,
        ProductionEvidenceCategory.SENTINEL_AUDIT,
        ProductionEvidenceCategory.INCIDENT_REGISTER,
    }
)


class ProductionEvidenceArtifact(DomainModel):
    category: ProductionEvidenceCategory
    reference_id: Identifier
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0, le=268_435_456)
    captured_start_ts_ns: int = Field(ge=0)
    captured_end_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def path_and_interval_are_bounded(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "raw"
        ):
            raise ValueError("production evidence artifacts must be below raw/ without traversal")
        if self.captured_end_ts_ns < self.captured_start_ts_ns:
            raise ValueError("production evidence capture interval is reversed")
        return self


class NativeDrillCheck(DomainModel):
    check_id: Identifier
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=2_048)]
    required: Annotated[str, Field(min_length=1, max_length=2_048)]


class NativeDrillEvidence(DomainModel):
    schema_version: Literal[1] = 1
    drill: RequiredNativeDrill
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    checks: tuple[NativeDrillCheck, ...] = Field(min_length=1, max_length=32)
    evidence_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        min_length=1,
        max_length=64,
    )
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_and_references_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("native drill must have a positive interval")
        check_ids = [check.check_id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("native drill check identities must be unique")
        if len(self.evidence_paths) != len(set(self.evidence_paths)):
            raise ValueError("native drill evidence paths must be unique")
        for value in self.evidence_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("native drill evidence paths cannot traverse")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("native drill invalidating events must be unique")
        return self

    @property
    def passed(self) -> bool:
        return not self.invalidating_events and all(check.passed for check in self.checks)


class ProductionIncidentSeverity(StrEnum):
    WARNING = "warning"
    MAJOR = "major"
    CRITICAL = "critical"


class ProductionIncident(DomainModel):
    incident_id: Identifier
    severity: ProductionIncidentSeverity
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int | None = Field(default=None, ge=0)
    resolved: bool
    evidence_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        min_length=1,
        max_length=64,
    )

    @model_validator(mode="after")
    def interval_and_references_are_valid(self) -> Self:
        if self.ended_ts_ns is not None and self.ended_ts_ns < self.started_ts_ns:
            raise ValueError("production incident interval is reversed")
        if self.resolved != (self.ended_ts_ns is not None):
            raise ValueError("production incident resolution and end timestamp differ")
        if len(self.evidence_paths) != len(set(self.evidence_paths)):
            raise ValueError("production incident evidence paths must be unique")
        for value in self.evidence_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("production incident evidence paths cannot traverse")
        return self


class ProductionIncidentRegister(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    admission_id: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]
    incidents: tuple[ProductionIncident, ...] = Field(default=(), max_length=4_096)

    @model_validator(mode="after")
    def interval_and_incidents_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("production incident register must cover a positive interval")
        if self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("production incident register must be reviewed after its interval")
        ids = [incident.incident_id for incident in self.incidents]
        if len(ids) != len(set(ids)):
            raise ValueError("production incident identities must be unique")
        for incident in self.incidents:
            end = self.ended_ts_ns if incident.ended_ts_ns is None else incident.ended_ts_ns
            if incident.started_ts_ns < self.started_ts_ns or end > self.ended_ts_ns:
                raise ValueError("production incident escapes the reviewed interval")
        return self


class ProductionEvidenceManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    deployment_id: Identifier
    admission_id: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    created_ts_ns: int = Field(ge=0)
    contains_credentials: Literal[False] = False
    artifacts: tuple[ProductionEvidenceArtifact, ...] = Field(min_length=21, max_length=4_096)

    @model_validator(mode="after")
    def interval_and_inventory_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("production evidence must cover a positive interval")
        if self.created_ts_ns < self.ended_ts_ns:
            raise ValueError("production evidence cannot be created before its interval ends")
        paths = [artifact.relative_path for artifact in self.artifacts]
        identities = [(artifact.category, artifact.reference_id) for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("production evidence artifact paths must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("production evidence category references must be unique")
        by_category = {
            category: tuple(item for item in self.artifacts if item.category is category)
            for category in ProductionEvidenceCategory
        }
        if any(len(by_category[category]) != 1 for category in PRODUCTION_EVIDENCE_SINGLETONS):
            raise ValueError("production evidence singleton inventory is incomplete or duplicated")
        release_ids = {
            item.reference_id for item in by_category[ProductionEvidenceCategory.RELEASE_ARTIFACT]
        }
        if release_ids != {kind.value for kind in DeploymentArtifactKind}:
            raise ValueError("production release artifact inventory is incomplete or unexpected")
        drill_ids = {
            item.reference_id for item in by_category[ProductionEvidenceCategory.DRILL_REPORT]
        }
        if drill_ids != {drill.value for drill in RequiredNativeDrill}:
            raise ValueError("production drill report inventory is incomplete or unexpected")
        renewal_ids = {
            item.reference_id
            for item in by_category[ProductionEvidenceCategory.AUTHORIZATION_RENEWAL]
        }
        renewal_signature_ids = {
            item.reference_id
            for item in by_category[ProductionEvidenceCategory.AUTHORIZATION_RENEWAL_SIGNATURE]
        }
        if renewal_ids != renewal_signature_ids:
            raise ValueError("production renewal and signature inventories differ")
        for category in (
            ProductionEvidenceCategory.EXECUTION_AUDIT,
            ProductionEvidenceCategory.SENTINEL_AUDIT,
            ProductionEvidenceCategory.INCIDENT_REGISTER,
        ):
            binding = by_category[category][0]
            if (
                binding.captured_start_ts_ns > self.started_ts_ns
                or binding.captured_end_ts_ns < self.ended_ts_ns
            ):
                raise ValueError(f"{category.value} does not cover the production interval")
        return self


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
    schema_version: Literal[2] = 2
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    assembled_ts_ns: int = Field(ge=0)
    retention_expires_ts_ns: int = Field(gt=0)
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    final_tag_commit_sha: GitCommit
    contains_credentials: Literal[False] = False
    restore_test_passed: Literal[True] = True
    credential_scan_policy_id: Identifier
    credential_scan_policy_sha256: Sha256
    evidence_manifest_sha256: Sha256
    evidence_bundle_sha256: Sha256
    restore_evidence_sha256: Sha256
    final_tag_evidence_sha256: Sha256
    artifacts: tuple[LegacyArchiveArtifact, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )

    @model_validator(mode="after")
    def inventory_and_tag_are_exact(self) -> Self:
        if self.assembled_ts_ns < self.created_ts_ns:
            raise ValueError("legacy archive assembly cannot predate archive creation")
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


class LegacyArchiveControlKind(StrEnum):
    RESTORE_EVIDENCE = "restore_evidence"
    CREDENTIAL_SCAN_EVIDENCE = "credential_scan_evidence"
    FINAL_TAG_EVIDENCE = "final_tag_evidence"


REQUIRED_ARCHIVE_CONTROLS = frozenset(LegacyArchiveControlKind)


class LegacyArchiveControlArtifact(DomainModel):
    kind: LegacyArchiveControlKind
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0, le=16_777_216)
    captured_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def path_is_bounded_to_controls(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "controls"
        ):
            raise ValueError("legacy archive controls must be below controls/ without traversal")
        return self


class LegacyArchiveRestoreCheck(DomainModel):
    kind: LegacyArchiveArtifactKind
    source_sha256: Sha256
    source_byte_count: int = Field(gt=0, le=1_099_511_627_776)
    restored_sha256: Sha256
    restored_byte_count: int = Field(gt=0, le=1_099_511_627_776)
    restored: Literal[True] = True

    @model_validator(mode="after")
    def restored_bytes_match_source(self) -> Self:
        if (
            self.restored_sha256 != self.source_sha256
            or self.restored_byte_count != self.source_byte_count
        ):
            raise ValueError("restored legacy archive bytes differ from their source")
        return self


class LegacyArchiveRestoreEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]
    isolated_restore_completed: Literal[True] = True
    checks: tuple[LegacyArchiveRestoreCheck, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_and_checks_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("legacy archive restore interval or review time is invalid")
        kinds = [check.kind for check in self.checks]
        if len(kinds) != len(set(kinds)) or set(kinds) != REQUIRED_ARCHIVE_ARTIFACTS:
            raise ValueError("legacy archive restore must check every category exactly once")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("legacy archive restore invalidating events must be unique")
        return self

    @property
    def passed(self) -> bool:
        return not self.invalidating_events


class LegacyCredentialDetector(StrEnum):
    API_TOKEN = "api_token"
    PASSWORD = "password"
    PRIVATE_KEY = "private_key"
    SEED_PHRASE = "seed_phrase"
    SESSION_CREDENTIAL = "session_credential"


REQUIRED_LEGACY_CREDENTIAL_DETECTORS = frozenset(LegacyCredentialDetector)


class LegacyArchiveCredentialScanPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    recursive_archive_scan: Literal[True] = True
    maximum_findings: Literal[0] = 0
    required_detectors: tuple[LegacyCredentialDetector, ...] = Field(
        min_length=len(REQUIRED_LEGACY_CREDENTIAL_DETECTORS),
        max_length=len(REQUIRED_LEGACY_CREDENTIAL_DETECTORS),
    )

    @model_validator(mode="after")
    def detectors_are_exact(self) -> Self:
        if set(self.required_detectors) != REQUIRED_LEGACY_CREDENTIAL_DETECTORS:
            raise ValueError("legacy credential scan policy must require every detector")
        return self


class LegacyArchiveCredentialScanCheck(DomainModel):
    kind: LegacyArchiveArtifactKind
    artifact_sha256: Sha256
    recursive_scan_completed: Literal[True] = True
    finding_count: Literal[0] = 0


class LegacyArchiveCredentialScanEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]
    scanner_name: Annotated[str, Field(min_length=1, max_length=128)]
    scanner_version: Annotated[str, Field(min_length=1, max_length=128)]
    policy_id: Identifier
    policy_sha256: Sha256
    checks: tuple[LegacyArchiveCredentialScanCheck, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )
    findings: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_and_checks_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("legacy credential scan interval or review time is invalid")
        kinds = [check.kind for check in self.checks]
        if len(kinds) != len(set(kinds)) or set(kinds) != REQUIRED_ARCHIVE_ARTIFACTS:
            raise ValueError("legacy credential scan must check every category exactly once")
        if self.findings:
            raise ValueError("legacy archive credential scan contains findings")
        return self


class LegacyFinalTagEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    captured_ts_ns: int = Field(ge=0)
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    final_tag_commit_sha: GitCommit
    annotated_tag: Literal[True] = True
    tag_object_sha: GitCommit
    verification_output_sha256: Sha256
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def tag_resolves_to_source(self) -> Self:
        if self.final_tag_commit_sha != self.source_commit_sha:
            raise ValueError("retained mt5-final tag does not resolve to the source commit")
        if self.tag_object_sha == self.final_tag_commit_sha:
            raise ValueError("retained mt5-final must be an annotated tag object")
        return self


class LegacyArchiveEvidenceManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    retention_expires_ts_ns: int = Field(gt=0)
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    final_tag_commit_sha: GitCommit
    contains_credentials: Literal[False] = False
    artifacts: tuple[LegacyArchiveArtifact, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
        max_length=len(REQUIRED_ARCHIVE_ARTIFACTS),
    )
    controls: tuple[LegacyArchiveControlArtifact, ...] = Field(
        min_length=len(REQUIRED_ARCHIVE_CONTROLS),
        max_length=len(REQUIRED_ARCHIVE_CONTROLS),
    )

    @model_validator(mode="after")
    def inventory_and_intervals_are_exact(self) -> Self:
        if self.retention_expires_ts_ns <= self.created_ts_ns:
            raise ValueError("legacy archive evidence retention must follow creation")
        if self.final_tag_commit_sha != self.source_commit_sha:
            raise ValueError("legacy archive evidence tag must resolve to source")
        artifact_kinds = [artifact.kind for artifact in self.artifacts]
        artifact_paths = [artifact.relative_path for artifact in self.artifacts]
        control_kinds = [control.kind for control in self.controls]
        control_paths = [control.relative_path for control in self.controls]
        if (
            len(artifact_kinds) != len(set(artifact_kinds))
            or set(artifact_kinds) != REQUIRED_ARCHIVE_ARTIFACTS
        ):
            raise ValueError("legacy archive evidence category inventory is not exact")
        if (
            len(control_kinds) != len(set(control_kinds))
            or set(control_kinds) != REQUIRED_ARCHIVE_CONTROLS
        ):
            raise ValueError("legacy archive evidence control inventory is not exact")
        paths = (*artifact_paths, *control_paths)
        if len(paths) != len(set(paths)):
            raise ValueError("legacy archive evidence paths must be unique")
        if any(item.captured_ts_ns > self.created_ts_ns for item in self.artifacts) or any(
            item.captured_ts_ns > self.created_ts_ns for item in self.controls
        ):
            raise ValueError("legacy archive evidence cannot be captured after creation")
        return self


class NativeProductionObservation(DomainModel):
    schema_version: Literal[3] = 3
    retirement_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    deployment_id: Identifier
    admission_id: Sha256
    terminal_authorization_id: Sha256
    renewal_count: int = Field(ge=0)
    authorization_expires_ts_ns: int = Field(gt=0)
    authorization_chain_sha256: Sha256
    approval_key_id: Identifier
    approval_public_key_sha256: Sha256
    production_approval_sha256: Sha256
    production_artifact_manifest_sha256: Sha256
    evidence_manifest_sha256: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    assembled_ts_ns: int = Field(ge=0)
    sentinel_health_samples: int = Field(gt=0)
    maximum_operational_gap_ns: int = Field(gt=0)
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
        if self.assembled_ts_ns < self.ended_ts_ns:
            raise ValueError("native production observation cannot predate its interval end")
        if self.authorization_expires_ts_ns <= self.ended_ts_ns:
            raise ValueError(
                "native production authorization must remain active through observation"
            )
        if self.authorization_expires_ts_ns <= self.assembled_ts_ns:
            raise ValueError("native production authorization must be active at assembly")
        minimum_samples = max(
            1,
            (self.ended_ts_ns - self.started_ts_ns + self.maximum_operational_gap_ns - 1)
            // self.maximum_operational_gap_ns
            - 1,
        )
        if self.sentinel_health_samples < minimum_samples:
            raise ValueError("native production health samples cannot cover the reported interval")
        if self.renewal_count == 0 and self.terminal_authorization_id != self.admission_id:
            raise ValueError("unrenewed native authorization must equal admission identity")
        if self.renewal_count > 0 and self.terminal_authorization_id == self.admission_id:
            raise ValueError("renewed native authorization must bind the terminal renewal")
        if set(self.completed_drills) != REQUIRED_NATIVE_DRILLS:
            raise ValueError("native production observation must contain every retirement drill")
        return self


class LegacyAccountMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"
    CONTEST = "contest"
    UNKNOWN = "unknown"


class LegacyManagedPositionEvidence(DomainModel):
    position_id_sha256: Sha256
    instrument_id: Literal["XAUUSD"] = "XAUUSD"


class LegacyFinalTradeReportEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    capture_id: Identifier
    captured_ts_ns: int = Field(ge=0)
    generated_by: Identifier
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    source_report_sha256: Sha256
    account_login_sha256: Sha256
    broker_server_sha256: Sha256
    instrument_id: Literal["XAUUSD"] = "XAUUSD"
    mt5_reported_open_positions: int = Field(ge=0, le=4_096)
    entry_pause_reported: bool
    final_status_sha256: Sha256
    managed_positions: tuple[LegacyManagedPositionEvidence, ...] = Field(
        default=(), max_length=4_096
    )

    @model_validator(mode="after")
    def managed_positions_are_unique(self) -> Self:
        if self.generated_by == self.reviewed_by:
            raise ValueError("final MT5 trade report requires an independent reviewer")
        identities = [item.position_id_sha256 for item in self.managed_positions]
        if len(identities) != len(set(identities)):
            raise ValueError("final MT5 trade report contains duplicate managed positions")
        if len(self.managed_positions) > self.mt5_reported_open_positions:
            raise ValueError("managed positions exceed MT5 reported account positions")
        return self


class LegacyBrokerPositionEvidence(DomainModel):
    position_id_sha256: Sha256
    instrument_id: Identifier


class LegacyBrokerOrderEvidence(DomainModel):
    order_id_sha256: Sha256
    instrument_id: Identifier


class LegacyBrokerAccountStateEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    capture_id: Identifier
    captured_ts_ns: int = Field(ge=0)
    captured_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    account_mode: LegacyAccountMode
    account_login_sha256: Sha256
    broker_server_sha256: Sha256
    source_export_sha256: Sha256
    positions: tuple[LegacyBrokerPositionEvidence, ...] = Field(default=(), max_length=4_096)
    pending_orders: tuple[LegacyBrokerOrderEvidence, ...] = Field(default=(), max_length=4_096)

    @model_validator(mode="after")
    def inventory_is_unique_and_independently_reviewed(self) -> Self:
        if self.captured_by == self.reviewed_by:
            raise ValueError("broker account state requires an independent reviewer")
        positions = [item.position_id_sha256 for item in self.positions]
        orders = [item.order_id_sha256 for item in self.pending_orders]
        if len(positions) != len(set(positions)):
            raise ValueError("broker account state contains duplicate positions")
        if len(orders) != len(set(orders)):
            raise ValueError("broker account state contains duplicate pending orders")
        return self


class LegacyCommandWriterSurface(StrEnum):
    PROCESS_TABLE = "process_table"
    PM2 = "pm2"
    CRON = "cron"
    SYSTEMD = "systemd"
    COMMAND_FILE_HANDLES = "command_file_handles"


REQUIRED_COMMAND_WRITER_SURFACES = frozenset(LegacyCommandWriterSurface)


class LegacyCommandWriterCheck(DomainModel):
    surface: LegacyCommandWriterSurface
    evidence_member: Annotated[str, Field(min_length=1, max_length=256)]
    evidence_sha256: Sha256
    active_writer_ids: tuple[Identifier, ...] = Field(default=(), max_length=1_024)

    @model_validator(mode="after")
    def writer_identities_are_unique(self) -> Self:
        expected_member = f"final-state/command-writers/{self.surface.value}.txt"
        if self.evidence_member != expected_member:
            raise ValueError("command writer evidence member does not match its surface")
        if len(self.active_writer_ids) != len(set(self.active_writer_ids)):
            raise ValueError("command writer evidence contains duplicate identities")
        return self


class LegacyServiceConfigurationEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    capture_id: Identifier
    captured_ts_ns: int = Field(ge=0)
    captured_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    final_status_sha256: Sha256
    entry_pause_file_present: bool
    entry_pause_file_sha256: Sha256 | None = None
    ea_entry_pause_reported: bool
    command_writer_checks: tuple[LegacyCommandWriterCheck, ...] = Field(
        min_length=len(REQUIRED_COMMAND_WRITER_SURFACES),
        max_length=len(REQUIRED_COMMAND_WRITER_SURFACES),
    )

    @model_validator(mode="after")
    def writer_surfaces_are_exact_and_independently_reviewed(self) -> Self:
        if self.captured_by == self.reviewed_by:
            raise ValueError("service configuration state requires an independent reviewer")
        if self.entry_pause_file_present != (self.entry_pause_file_sha256 is not None):
            raise ValueError("entry-pause file presence and hash must agree")
        surfaces = [item.surface for item in self.command_writer_checks]
        if len(surfaces) != len(set(surfaces)) or set(surfaces) != REQUIRED_COMMAND_WRITER_SURFACES:
            raise ValueError("service state must inspect every command writer surface exactly once")
        return self


class LegacyFinalState(DomainModel):
    schema_version: Literal[2] = 2
    retirement_id: Identifier
    captured_ts_ns: int = Field(ge=0)
    assembled_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    archive_manifest_sha256: Sha256
    archive_bundle_sha256: Sha256
    account_mode: LegacyAccountMode
    account_login_sha256: Sha256
    broker_server_sha256: Sha256
    instrument_id: Literal["XAUUSD"] = "XAUUSD"
    open_managed_positions: int = Field(ge=0, le=4_096)
    open_unmanaged_positions: int = Field(ge=0, le=4_096)
    pending_orders: int = Field(ge=0, le=4_096)
    entry_pause_active: bool
    command_file_writer_count: int = Field(ge=0, le=5_120)
    final_trade_report_sha256: Sha256
    final_status_sha256: Sha256
    broker_account_state_sha256: Sha256
    service_configuration_sha256: Sha256
    final_trade_report_capture_id: Identifier
    broker_account_capture_id: Identifier
    service_configuration_capture_id: Identifier

    @model_validator(mode="after")
    def assembly_follows_capture(self) -> Self:
        if self.assembled_ts_ns < self.captured_ts_ns:
            raise ValueError("final legacy state assembly cannot predate its evidence")
        return self


class RetirementReadinessObservation(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    observed_ts_ns: int = Field(ge=0)
    native: NativeProductionObservation
    archive: LegacyArchiveManifest
    legacy: LegacyFinalState

    @model_validator(mode="after")
    def identities_and_evidence_bind(self) -> Self:
        if self.native.retirement_id != self.retirement_id:
            raise ValueError("retirement observation and native identities differ")
        if self.archive.retirement_id != self.retirement_id:
            raise ValueError("retirement observation and archive identities differ")
        if self.legacy.retirement_id != self.retirement_id:
            raise ValueError("retirement observation and final legacy state identities differ")
        if self.observed_ts_ns < self.native.ended_ts_ns:
            raise ValueError("retirement readiness cannot predate native observation completion")
        if self.observed_ts_ns >= self.native.authorization_expires_ts_ns:
            raise ValueError("retirement readiness requires an active native authorization")
        if self.observed_ts_ns < self.archive.created_ts_ns:
            raise ValueError("retirement readiness cannot predate archive creation")
        if self.observed_ts_ns < self.archive.assembled_ts_ns:
            raise ValueError("retirement readiness cannot predate archive assembly")
        if self.observed_ts_ns < self.legacy.captured_ts_ns:
            raise ValueError("retirement readiness cannot predate final legacy state")
        if self.observed_ts_ns < self.legacy.assembled_ts_ns:
            raise ValueError("retirement readiness cannot predate final legacy state assembly")
        if (
            self.legacy.archive_manifest_sha256 != self.archive.sha256()
            or self.legacy.archive_bundle_sha256 != self.archive.evidence_bundle_sha256
        ):
            raise ValueError("final legacy state is not bound to the retained archive")
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
        if (
            by_kind[LegacyArchiveArtifactKind.SERVICE_CONFIGURATION].content_sha256
            != self.legacy.service_configuration_sha256
        ):
            raise ValueError("service configuration state is not bound into the archive")
        return self


class RetirementPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_native_production_observation_ns: int = Field(gt=0)
    maximum_native_operational_gap_ns: int = Field(gt=0)
    minimum_disabled_observation_ns: int = Field(gt=0)
    minimum_archive_retention_ns: int = Field(gt=0)
    maximum_final_state_capture_skew_ns: int = Field(gt=0)
    maximum_final_state_age_ns: int = Field(gt=0)
    archive_credential_scan_policy_id: Identifier
    archive_credential_scan_policy_sha256: Sha256
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
    FINAL_STATE_FRESHNESS = "final_state_freshness"
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
