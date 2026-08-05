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
    maximum_disabled_evidence_gap_ns: int = Field(gt=0)
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


class DisabledEvidenceArtifactKind(StrEnum):
    STOP_EXECUTION_OUTPUT = "stop_execution_output"
    CAPABILITY_SNAPSHOT = "capability_snapshot"
    BROKER_ORDER_EXPORT = "broker_order_export"
    CREDENTIAL_QUARANTINE_AUDIT = "credential_quarantine_audit"
    NATIVE_OPERATIONAL_AUDIT = "native_operational_audit"


REQUIRED_DISABLED_EVIDENCE_ARTIFACT_KINDS = frozenset(DisabledEvidenceArtifactKind)


class DisabledEvidenceArtifact(DomainModel):
    artifact_id: Identifier
    kind: DisabledEvidenceArtifactKind
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
            raise ValueError("disabled evidence artifacts must be below raw/ without traversal")
        if self.captured_end_ts_ns < self.captured_start_ts_ns:
            raise ValueError("disabled evidence artifact interval is reversed")
        return self


class DisabledEvidenceControlKind(StrEnum):
    STOP_EXECUTION = "stop_execution"
    CAPABILITY_AUDIT = "capability_audit"
    BROKER_ORDER_AUDIT = "broker_order_audit"
    CREDENTIAL_QUARANTINE = "credential_quarantine"
    NATIVE_STABILITY_AUDIT = "native_stability_audit"
    CREDENTIAL_SCAN = "credential_scan"


REQUIRED_DISABLED_EVIDENCE_CONTROLS = frozenset(DisabledEvidenceControlKind)


class DisabledEvidenceControl(DomainModel):
    kind: DisabledEvidenceControlKind
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
            raise ValueError("disabled evidence controls must be below controls/ without traversal")
        return self


class RequiredLegacyStopAction(StrEnum):
    STOP_WATCHDOG = "stop_watchdog"
    STOP_REVIEW = "stop_review"
    STOP_MT5 = "stop_mt5"
    STOP_DASHBOARD = "stop_dashboard"
    REMOVE_PM2_STARTUP = "remove_pm2_startup"
    DISABLE_CRON = "disable_cron"
    DISABLE_NGINX_ROUTE = "disable_nginx_route"
    DISABLE_LOGROTATE = "disable_logrotate"
    DISABLE_MT5_AUTOSTART = "disable_mt5_autostart"
    QUARANTINE_CREDENTIALS = "quarantine_credentials"
    VERIFY_ZERO_MT5_PROCESSES = "verify_zero_mt5_processes"
    VERIFY_ZERO_COMMAND_WRITERS = "verify_zero_command_writers"
    VERIFY_ZERO_BROKER_ORDERS = "verify_zero_broker_orders"


REQUIRED_LEGACY_STOP_ACTIONS = tuple(RequiredLegacyStopAction)


class LegacyStopActionEvidence(DomainModel):
    action: RequiredLegacyStopAction
    completed_ts_ns: int = Field(ge=0)
    succeeded: Literal[True] = True
    evidence_path: Annotated[str, Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def evidence_path_is_safe(self) -> Self:
        path = PurePosixPath(self.evidence_path)
        if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "raw":
            raise ValueError("legacy stop action evidence must reference raw/ without traversal")
        return self


class LegacyStopExecutionEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    readiness_report_sha256: Sha256
    stop_approval_sha256: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    operator: Annotated[str, Field(min_length=1, max_length=256)]
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]
    actions: tuple[LegacyStopActionEvidence, ...] = Field(
        min_length=len(REQUIRED_LEGACY_STOP_ACTIONS),
        max_length=len(REQUIRED_LEGACY_STOP_ACTIONS),
    )
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_order_and_review_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("legacy stop execution must have a positive interval")
        if self.operator == self.reviewer:
            raise ValueError("legacy stop execution requires an independent reviewer")
        if tuple(item.action for item in self.actions) != REQUIRED_LEGACY_STOP_ACTIONS:
            raise ValueError("legacy stop execution actions must be exact and ordered")
        timestamps = tuple(item.completed_ts_ns for item in self.actions)
        if (
            timestamps != tuple(sorted(timestamps))
            or timestamps[0] < self.started_ts_ns
            or timestamps[-1] > self.ended_ts_ns
        ):
            raise ValueError("legacy stop execution action timing is invalid")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("legacy stop execution invalidating events must be unique")
        if self.invalidating_events:
            raise ValueError("legacy stop execution contains invalidating events")
        return self


class LegacyCapabilityState(DomainModel):
    capability: LegacyCapability
    disabled: bool
    active_instance_count: int = Field(ge=0, le=65_536)


class LegacyCapabilitySample(DomainModel):
    observed_ts_ns: int = Field(ge=0)
    evidence_path: Annotated[str, Field(min_length=1, max_length=512)]
    states: tuple[LegacyCapabilityState, ...] = Field(
        min_length=len(REQUIRED_DISABLED_CAPABILITIES),
        max_length=len(REQUIRED_DISABLED_CAPABILITIES),
    )

    @model_validator(mode="after")
    def capability_set_and_path_are_exact(self) -> Self:
        capabilities = [item.capability for item in self.states]
        if (
            len(capabilities) != len(set(capabilities))
            or set(capabilities) != REQUIRED_DISABLED_CAPABILITIES
        ):
            raise ValueError("disabled capability sample must cover every capability")
        path = PurePosixPath(self.evidence_path)
        if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "raw":
            raise ValueError("disabled capability sample must reference raw/ without traversal")
        return self


class LegacyCapabilityAuditEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    samples: tuple[LegacyCapabilitySample, ...] = Field(min_length=2, max_length=8_192)
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_samples_and_review_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("legacy capability audit interval or review time is invalid")
        if self.collected_by == self.reviewed_by:
            raise ValueError("legacy capability audit requires an independent reviewer")
        timestamps = tuple(item.observed_ts_ns for item in self.samples)
        if timestamps != tuple(sorted(set(timestamps))):
            raise ValueError("legacy capability samples must be unique and ordered")
        if timestamps[0] < self.started_ts_ns or timestamps[-1] > self.ended_ts_ns:
            raise ValueError("legacy capability sample escapes the audited interval")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("legacy capability invalidating events must be unique")
        return self


class LegacyPostStopBrokerOrder(DomainModel):
    order_id_sha256: Sha256
    instrument_id: Identifier
    created_ts_ns: int = Field(ge=0)


class LegacyBrokerOrderAuditEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    queried_start_ts_ns: int = Field(ge=0)
    queried_end_ts_ns: int = Field(ge=0)
    captured_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    captured_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    account_login_sha256: Sha256
    broker_server_sha256: Sha256
    coverage_complete: bool
    source_evidence_path: Annotated[str, Field(min_length=1, max_length=512)]
    orders: tuple[LegacyPostStopBrokerOrder, ...] = Field(default=(), max_length=100_000)

    @model_validator(mode="after")
    def interval_inventory_and_review_are_valid(self) -> Self:
        if (
            self.queried_end_ts_ns <= self.queried_start_ts_ns
            or self.captured_ts_ns < self.queried_end_ts_ns
            or self.reviewed_ts_ns < self.captured_ts_ns
        ):
            raise ValueError("legacy broker order audit interval is invalid")
        if self.captured_by == self.reviewed_by:
            raise ValueError("legacy broker order audit requires an independent reviewer")
        order_ids = [item.order_id_sha256 for item in self.orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("legacy broker order audit contains duplicate orders")
        if any(
            not self.queried_start_ts_ns <= item.created_ts_ns <= self.queried_end_ts_ns
            for item in self.orders
        ):
            raise ValueError("legacy broker order escapes the queried interval")
        path = PurePosixPath(self.source_evidence_path)
        if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "raw":
            raise ValueError("legacy broker order audit must reference raw/ without traversal")
        return self


class LegacyCredentialQuarantineCheck(DomainModel):
    credential_id: Identifier
    quarantined: bool
    active_reader_count: int = Field(ge=0, le=65_536)
    evidence_path: Annotated[str, Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def evidence_path_is_safe(self) -> Self:
        path = PurePosixPath(self.evidence_path)
        if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "raw":
            raise ValueError("credential quarantine check must reference raw/ without traversal")
        return self


class LegacyCredentialQuarantineEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    started_ts_ns: int = Field(ge=0)
    observed_through_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    inventory_complete: bool
    continuous_audit: bool
    checks: tuple[LegacyCredentialQuarantineCheck, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def interval_inventory_and_review_are_valid(self) -> Self:
        if (
            self.observed_through_ts_ns <= self.started_ts_ns
            or self.reviewed_ts_ns < self.observed_through_ts_ns
        ):
            raise ValueError("credential quarantine interval or review time is invalid")
        if self.collected_by == self.reviewed_by:
            raise ValueError("credential quarantine requires an independent reviewer")
        identities = [item.credential_id for item in self.checks]
        if len(identities) != len(set(identities)):
            raise ValueError("credential quarantine inventory contains duplicates")
        return self


class NativeDisabledWindowEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    native_deployment_id: Identifier
    native_admission_id: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    continuous_monitoring: bool
    critical_incidents: int = Field(ge=0)
    reconciliation_failures: int = Field(ge=0)
    risk_breaches: int = Field(ge=0)
    evidence_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        min_length=1,
        max_length=1_024,
    )

    @model_validator(mode="after")
    def interval_references_and_review_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("native disabled-window audit interval or review time is invalid")
        if self.collected_by == self.reviewed_by:
            raise ValueError("native disabled-window audit requires an independent reviewer")
        if len(self.evidence_paths) != len(set(self.evidence_paths)):
            raise ValueError("native disabled-window evidence paths must be unique")
        for value in self.evidence_paths:
            path = PurePosixPath(value)
            if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "raw":
                raise ValueError("native disabled-window audit must reference raw/ evidence")
        return self


class DisabledCredentialScanCheck(DomainModel):
    artifact_id: Identifier
    artifact_sha256: Sha256
    recursive_scan_completed: Literal[True] = True
    finding_count: Literal[0] = 0


class DisabledCredentialScanEvidence(DomainModel):
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
    checks: tuple[DisabledCredentialScanCheck, ...] = Field(min_length=5, max_length=8_192)
    findings: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_checks_and_findings_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("disabled credential scan interval or review time is invalid")
        artifact_ids = [item.artifact_id for item in self.checks]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("disabled credential scan contains duplicate artifact checks")
        if self.findings:
            raise ValueError("disabled credential scan contains findings")
        return self


class DisabledEvidenceManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    readiness_report_sha256: Sha256
    stop_approval_sha256: Sha256
    archive_manifest_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    contains_credentials: Literal[False] = False
    artifacts: tuple[DisabledEvidenceArtifact, ...] = Field(min_length=5, max_length=8_192)
    controls: tuple[DisabledEvidenceControl, ...] = Field(
        min_length=len(REQUIRED_DISABLED_EVIDENCE_CONTROLS),
        max_length=len(REQUIRED_DISABLED_EVIDENCE_CONTROLS),
    )

    @model_validator(mode="after")
    def interval_and_inventory_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.created_ts_ns < self.ended_ts_ns:
            raise ValueError("disabled evidence interval or creation time is invalid")
        artifact_ids = [item.artifact_id for item in self.artifacts]
        artifact_paths = [item.relative_path for item in self.artifacts]
        control_kinds = [item.kind for item in self.controls]
        control_paths = [item.relative_path for item in self.controls]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("disabled evidence artifact identities must be unique")
        if set(item.kind for item in self.artifacts) != REQUIRED_DISABLED_EVIDENCE_ARTIFACT_KINDS:
            raise ValueError("disabled evidence artifact categories are incomplete")
        if (
            len(control_kinds) != len(set(control_kinds))
            or set(control_kinds) != REQUIRED_DISABLED_EVIDENCE_CONTROLS
        ):
            raise ValueError("disabled evidence control inventory is not exact")
        paths = (*artifact_paths, *control_paths)
        if len(paths) != len(set(paths)):
            raise ValueError("disabled evidence paths must be unique")
        if any(item.captured_end_ts_ns > self.created_ts_ns for item in self.artifacts) or any(
            item.captured_ts_ns > self.created_ts_ns for item in self.controls
        ):
            raise ValueError("disabled evidence cannot be captured after manifest creation")
        return self


class LegacyCapabilityObservation(DomainModel):
    capability: LegacyCapability
    disabled: bool
    active_instance_count: int = Field(ge=0)
    evidence_sha256: Sha256


class DisabledObservation(DomainModel):
    schema_version: Literal[2] = 2
    retirement_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    assembled_ts_ns: int = Field(ge=0)
    readiness_report_sha256: Sha256
    stop_approval_sha256: Sha256
    stop_approval_verification_id: Sha256
    archive_manifest_sha256: Sha256
    archive_bundle_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    native_observation_sha256: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    capability_sample_count: int = Field(ge=2, le=8_192)
    maximum_capability_gap_ns: int = Field(gt=0)
    capability_audit_invalidating_events: int = Field(ge=0)
    native_critical_incidents: int = Field(ge=0)
    native_reconciliation_failures: int = Field(ge=0)
    native_risk_breaches: int = Field(ge=0)
    native_audit_complete: bool
    legacy_broker_orders_after_stop: int = Field(ge=0)
    legacy_broker_order_audit_complete: bool
    archive_reverified: Literal[True] = True
    legacy_credentials_quarantined: bool
    capabilities: tuple[LegacyCapabilityObservation, ...] = Field(
        min_length=len(REQUIRED_DISABLED_CAPABILITIES),
        max_length=len(REQUIRED_DISABLED_CAPABILITIES),
    )
    evidence_manifest_sha256: Sha256
    credential_scan_sha256: Sha256
    evidence_bundle_sha256: Sha256

    @model_validator(mode="after")
    def interval_and_capabilities_are_exact(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("disabled observation must have a positive interval")
        if self.assembled_ts_ns < self.ended_ts_ns:
            raise ValueError("disabled observation assembly cannot predate its interval")
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
    EVIDENCE_CONTINUITY = "evidence_continuity"
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
    schema_version: Literal[2] = 2
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


class CleanupInventoryScope(StrEnum):
    MQL5_SOURCE = "mql5_source"
    LEGACY_LIFECYCLE = "legacy_lifecycle"
    LEGACY_RESEARCH = "legacy_research"
    LEGACY_OPERATIONS = "legacy_operations"
    LEGACY_TESTS_AND_DOCS = "legacy_tests_and_docs"
    RUNTIME_STATE = "runtime_state"
    HOST_DEPENDENCIES = "host_dependencies"
    CREDENTIALS_AND_SESSIONS = "credentials_and_sessions"
    NATIVE_PACKAGE_MIGRATION = "native_package_migration"


REQUIRED_CLEANUP_INVENTORY_SCOPES = frozenset(CleanupInventoryScope)


class CleanupPathObjectType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class CleanupPathInventoryEntry(DomainModel):
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    object_type: CleanupPathObjectType
    state_sha256: Sha256
    byte_count: int = Field(ge=0, le=268_435_456)
    mode: Annotated[str, StringConstraints(pattern=r"^[0-7]{4}$")]

    @model_validator(mode="after")
    def relative_path_is_explicit(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if self.relative_path != "." and (path.is_absolute() or ".." in path.parts):
            raise ValueError("cleanup path inventory entries must be relative without traversal")
        return self


class CleanupPathInventoryEvidence(DomainModel):
    schema_version: Literal[1] = 1
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    source_commit_sha: GitCommit | None = None
    captured_ts_ns: int = Field(ge=0)
    entries: tuple[CleanupPathInventoryEntry, ...] = Field(
        min_length=1,
        max_length=1_000_000,
    )

    @model_validator(mode="after")
    def inventory_is_ordered_and_source_bound(self) -> Self:
        paths = tuple(item.relative_path for item in self.entries)
        if paths != tuple(sorted(set(paths))) or paths[0] != ".":
            raise ValueError("cleanup path inventory entries must be unique, ordered, and rooted")
        if (self.kind is CleanupTargetKind.REPOSITORY_PATH) != (self.source_commit_sha is not None):
            raise ValueError("only repository path inventories bind a source commit")
        return self

    def state_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "kind": self.kind,
                "locator": self.locator,
                "source_commit_sha": self.source_commit_sha,
                "entries": [item.model_dump(mode="json") for item in self.entries],
            }
        )

    @property
    def total_bytes(self) -> int:
        return sum(item.byte_count for item in self.entries)


class CleanupPathState(DomainModel):
    state_kind: Literal["path"] = "path"
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    object_type: CleanupPathObjectType
    exists: Literal[True] = True
    inventory_sha256: Sha256
    entry_count: int = Field(gt=0, le=1_000_000)
    total_bytes: int = Field(ge=0, le=1_099_511_627_776)
    captured_ts_ns: int = Field(ge=0)
    raw_artifact_id: Identifier

    def expected_state_sha256(self) -> str:
        return self.inventory_sha256

    def artifact_ids(self) -> tuple[str, ...]:
        return (self.raw_artifact_id,)


class CleanupHostState(DomainModel):
    state_kind: Literal["host_dependency"] = "host_dependency"
    kind: Literal[CleanupTargetKind.HOST_INTEGRATION, CleanupTargetKind.HOST_PACKAGE]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    installed: Literal[True] = True
    installed_version: Annotated[str, Field(min_length=1, max_length=256)]
    configuration_sha256: Sha256
    ownership_sha256: Sha256
    owned_by_aiquanttrader: Literal[True] = True
    shared_consumer_count: Literal[0] = 0
    captured_ts_ns: int = Field(ge=0)
    raw_artifact_ids: tuple[Identifier, Identifier]

    @model_validator(mode="after")
    def raw_artifacts_are_distinct(self) -> Self:
        if len(set(self.raw_artifact_ids)) != 2:
            raise ValueError("host cleanup state requires distinct state and ownership evidence")
        return self

    def expected_state_sha256(self) -> str:
        return canonical_sha256(
            {
                "kind": self.kind,
                "locator": self.locator,
                "installed": self.installed,
                "installed_version": self.installed_version,
                "configuration_sha256": self.configuration_sha256,
                "ownership_sha256": self.ownership_sha256,
                "owned_by_aiquanttrader": self.owned_by_aiquanttrader,
                "shared_consumer_count": self.shared_consumer_count,
            }
        )

    def artifact_ids(self) -> tuple[str, ...]:
        return self.raw_artifact_ids


class CleanupSecretState(DomainModel):
    state_kind: Literal["secret_reference"] = "secret_reference"
    kind: Literal[CleanupTargetKind.SECRET_REFERENCE] = CleanupTargetKind.SECRET_REFERENCE
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    provider: Identifier
    provider_record_id_sha256: Sha256
    provider_state_sha256: Sha256
    active_sessions_sha256: Sha256
    secret_material_included: Literal[False] = False
    captured_ts_ns: int = Field(ge=0)
    raw_artifact_ids: tuple[Identifier, Identifier]

    @model_validator(mode="after")
    def raw_artifacts_are_distinct(self) -> Self:
        if len(set(self.raw_artifact_ids)) != 2:
            raise ValueError("secret cleanup state requires distinct provider and session evidence")
        return self

    def expected_state_sha256(self) -> str:
        return canonical_sha256(
            {
                "kind": self.kind,
                "locator": self.locator,
                "provider": self.provider,
                "provider_record_id_sha256": self.provider_record_id_sha256,
                "provider_state_sha256": self.provider_state_sha256,
                "active_sessions_sha256": self.active_sessions_sha256,
                "secret_material_included": self.secret_material_included,
            }
        )

    def artifact_ids(self) -> tuple[str, ...]:
        return self.raw_artifact_ids


CleanupTargetState = Annotated[
    CleanupPathState | CleanupHostState | CleanupSecretState,
    Field(discriminator="state_kind"),
]


class CleanupTargetEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    target_id: Identifier
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    rationale: Annotated[str, Field(min_length=1, max_length=512)]
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    state: CleanupTargetState
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def target_is_independently_reviewed_and_actionable(self) -> Self:
        if self.collected_by == self.reviewed_by:
            raise ValueError("cleanup target evidence requires an independent reviewer")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("cleanup target invalidating events must be unique")
        if self.invalidating_events:
            raise ValueError("cleanup target evidence contains invalidating events")
        LegacyCleanupTarget(
            target_id=self.target_id,
            kind=self.state.kind,
            locator=self.state.locator,
            action=self.action,
            destination_locator=self.destination_locator,
            expected_state_sha256=self.state.expected_state_sha256(),
            rationale=self.rationale,
        )
        return self

    def cleanup_target(self) -> LegacyCleanupTarget:
        return LegacyCleanupTarget(
            target_id=self.target_id,
            kind=self.state.kind,
            locator=self.state.locator,
            action=self.action,
            destination_locator=self.destination_locator,
            expected_state_sha256=self.state.expected_state_sha256(),
            rationale=self.rationale,
        )


class CleanupScopeCheck(DomainModel):
    scope: CleanupInventoryScope
    present: bool
    target_ids: tuple[Identifier, ...] = Field(default=(), max_length=2_048)
    evidence_artifact_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def presence_and_references_match(self) -> Self:
        if self.present != bool(self.target_ids):
            raise ValueError("cleanup scope presence must match its target inventory")
        if len(self.target_ids) != len(set(self.target_ids)):
            raise ValueError("cleanup scope target identities must be unique")
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("cleanup scope evidence identities must be unique")
        return self


class CleanupInventoryAuditEvidence(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    source_commit_sha: GitCommit
    observed_ts_ns: int = Field(ge=0)
    reviewed_ts_ns: int = Field(ge=0)
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    inventory_complete: Literal[True] = True
    scopes: tuple[CleanupScopeCheck, ...] = Field(
        min_length=len(REQUIRED_CLEANUP_INVENTORY_SCOPES),
        max_length=len(REQUIRED_CLEANUP_INVENTORY_SCOPES),
    )
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def scope_inventory_and_review_are_exact(self) -> Self:
        if self.reviewed_ts_ns < self.observed_ts_ns:
            raise ValueError("cleanup inventory review cannot predate observation")
        if self.collected_by == self.reviewed_by:
            raise ValueError("cleanup inventory audit requires an independent reviewer")
        scopes = [item.scope for item in self.scopes]
        if len(scopes) != len(set(scopes)) or set(scopes) != REQUIRED_CLEANUP_INVENTORY_SCOPES:
            raise ValueError("cleanup inventory audit must cover every scope exactly once")
        target_ids = [target_id for scope in self.scopes for target_id in scope.target_ids]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("cleanup targets must belong to exactly one inventory scope")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("cleanup inventory invalidating events must be unique")
        if self.invalidating_events:
            raise ValueError("cleanup inventory contains invalidating events")
        return self


class CleanupEvidenceArtifact(DomainModel):
    artifact_id: Identifier
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0, le=268_435_456)
    captured_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def path_is_bounded_to_raw(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] != "raw"
        ):
            raise ValueError("cleanup evidence artifacts must be below raw/ without traversal")
        return self


class CleanupEvidenceControlKind(StrEnum):
    INVENTORY_AUDIT = "inventory_audit"
    TARGET_STATE = "target_state"
    CREDENTIAL_SCAN = "credential_scan"


class CleanupEvidenceControl(DomainModel):
    kind: CleanupEvidenceControlKind
    reference_id: Identifier
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
            raise ValueError("cleanup evidence controls must be below controls/ without traversal")
        return self


class CleanupCredentialScanCheck(DomainModel):
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    recursive_scan_completed: Literal[True] = True
    finding_count: Literal[0] = 0

    @model_validator(mode="after")
    def path_is_bounded_to_bundle(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
            or path.parts[0] not in {"raw", "controls"}
        ):
            raise ValueError("cleanup scan checks must reference raw/ or controls/")
        return self


class CleanupCredentialScanEvidence(DomainModel):
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
    checks: tuple[CleanupCredentialScanCheck, ...] = Field(min_length=2, max_length=4_096)
    findings: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_checks_and_findings_are_valid(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns or self.reviewed_ts_ns < self.ended_ts_ns:
            raise ValueError("cleanup credential scan interval or review time is invalid")
        references = [item.relative_path for item in self.checks]
        if len(references) != len(set(references)):
            raise ValueError("cleanup credential scan contains duplicate checks")
        if self.findings:
            raise ValueError("cleanup credential scan contains findings")
        return self


class CleanupEvidenceManifest(DomainModel):
    schema_version: Literal[1] = 1
    retirement_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    created_ts_ns: int = Field(ge=0)
    source_commit_sha: GitCommit
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    credential_scan_policy_id: Identifier
    credential_scan_policy_sha256: Sha256
    contains_credentials: Literal[False] = False
    artifacts: tuple[CleanupEvidenceArtifact, ...] = Field(min_length=2, max_length=4_096)
    controls: tuple[CleanupEvidenceControl, ...] = Field(min_length=3, max_length=2_050)

    @model_validator(mode="after")
    def inventory_is_exact(self) -> Self:
        artifact_ids = [item.artifact_id for item in self.artifacts]
        paths = [item.relative_path for item in self.artifacts]
        control_refs = [(item.kind, item.reference_id) for item in self.controls]
        paths.extend(item.relative_path for item in self.controls)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("cleanup evidence artifact identities must be unique")
        if len(control_refs) != len(set(control_refs)):
            raise ValueError("cleanup evidence control references must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("cleanup evidence paths must be unique")
        kinds = [item.kind for item in self.controls]
        if kinds.count(CleanupEvidenceControlKind.INVENTORY_AUDIT) != 1:
            raise ValueError("cleanup evidence requires one inventory audit")
        if kinds.count(CleanupEvidenceControlKind.CREDENTIAL_SCAN) != 1:
            raise ValueError("cleanup evidence requires one credential scan")
        if any(item.captured_ts_ns > self.created_ts_ns for item in self.artifacts) or any(
            item.captured_ts_ns > self.created_ts_ns for item in self.controls
        ):
            raise ValueError("cleanup evidence cannot be captured after manifest creation")
        return self


class LegacyCleanupTarget(DomainModel):
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
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
            "\x00",
            "\\",
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
        if (
            self.kind is CleanupTargetKind.HOST_INTEGRATION
            and self.action is not CleanupAction.REMOVE
        ):
            raise ValueError("host integrations may only use the remove action")
        if (
            self.action is CleanupAction.REVOKE
            and self.kind is not CleanupTargetKind.SECRET_REFERENCE
        ):
            raise ValueError("only secret references may use the revoke action")
        if self.action is CleanupAction.MIGRATE_NATIVE:
            if self.kind is not CleanupTargetKind.REPOSITORY_PATH:
                raise ValueError("native migration requires a repository path target")
            if self.destination_locator is None:
                raise ValueError("native migration requires an explicit destination locator")
            destination = PurePosixPath(self.destination_locator)
            if (
                any(token in self.destination_locator for token in forbidden_tokens)
                or destination.is_absolute()
                or ".." in destination.parts
                or str(destination) in {"", "."}
                or destination == PurePosixPath(self.locator)
            ):
                raise ValueError(
                    "native migration destination must be a distinct safe repository path"
                )
        elif self.destination_locator is not None:
            raise ValueError("only native migration may declare a destination locator")
        if self.action is CleanupAction.RETAIN_ARCHIVE_ONLY and self.kind not in {
            CleanupTargetKind.REPOSITORY_PATH,
            CleanupTargetKind.RUNTIME_PATH,
        }:
            raise ValueError("archive-only cleanup requires a repository or runtime path")
        if self.action is CleanupAction.REMOVE and self.kind is CleanupTargetKind.SECRET_REFERENCE:
            raise ValueError("secret references cannot use the remove action")
        return self


class LegacyCleanupManifest(DomainModel):
    schema_version: Literal[3] = 3
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    evidence_manifest_sha256: Sha256
    credential_scan_sha256: Sha256
    evidence_bundle_sha256: Sha256
    targets: tuple[LegacyCleanupTarget, ...] = Field(min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def targets_are_unique(self) -> Self:
        ids = [target.target_id for target in self.targets]
        locators = [(target.kind, target.locator) for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("cleanup target identities must be unique")
        if len(locators) != len(set(locators)):
            raise ValueError("cleanup target locators must be unique within each kind")
        destinations = [
            target.destination_locator
            for target in self.targets
            if target.destination_locator is not None
        ]
        if len(destinations) != len(set(destinations)):
            raise ValueError("cleanup migration destinations must be unique")
        path_targets = {
            kind: tuple(
                PurePosixPath(target.locator) for target in self.targets if target.kind is kind
            )
            for kind in (
                CleanupTargetKind.REPOSITORY_PATH,
                CleanupTargetKind.RUNTIME_PATH,
                CleanupTargetKind.HOST_INTEGRATION,
            )
        }
        for kind, paths in path_targets.items():
            for index, left in enumerate(paths):
                for right in paths[index + 1 :]:
                    if left in right.parents or right in left.parents:
                        raise ValueError(f"{kind.value} cleanup target paths cannot overlap")
        repository_source_paths = path_targets[CleanupTargetKind.REPOSITORY_PATH]
        for destination_value in destinations:
            destination = PurePosixPath(destination_value)
            if any(
                destination == source
                or destination in source.parents
                or source in destination.parents
                for source in repository_source_paths
            ):
                raise ValueError("cleanup migration destinations cannot overlap cleanup sources")
        return self


class CleanupPreflightGate(StrEnum):
    POLICY_BOUND = "policy_bound"
    DISABLED_OBSERVATION_PASSED = "disabled_observation_passed"
    APPROVED_MANIFEST_REPLAYED = "approved_manifest_replayed"
    ACTION_EVIDENCE_REPLAYED = "action_evidence_replayed"
    ACTION_EVIDENCE_FRESH = "action_evidence_fresh"
    TARGET_INVENTORY_EXACT = "target_inventory_exact"
    TARGET_STATE_UNCHANGED = "target_state_unchanged"
    CLEANUP_APPROVAL_ACTIVE = "cleanup_approval_active"


class CleanupPreflightGateResult(DomainModel):
    gate: CleanupPreflightGate
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=2_048)]
    required: Annotated[str, Field(min_length=1, max_length=2_048)]


class CleanupPreflightTargetResult(DomainModel):
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    expected_state_sha256: Sha256
    observed_state_sha256: Sha256
    state_matches: bool


class CleanupPreflightReceipt(DomainModel):
    """Short-lived evidence receipt; it never performs or expands cleanup authority."""

    schema_version: Literal[1] = 1
    receipt_id: Sha256
    retirement_id: Identifier
    evaluated_ts_ns: int = Field(ge=0)
    valid_until_ts_ns: int = Field(gt=0)
    action_capture_start_ts_ns: int = Field(ge=0)
    action_capture_end_ts_ns: int = Field(ge=0)
    action_state_expires_ts_ns: int = Field(gt=0)
    approval_expires_ts_ns: int = Field(gt=0)
    policy_id: Identifier
    policy_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    archive_manifest_sha256: Sha256
    approved_cleanup_manifest_sha256: Sha256
    action_snapshot_sha256: Sha256
    action_evidence_manifest_sha256: Sha256
    action_evidence_bundle_sha256: Sha256
    cleanup_approval_sha256: Sha256
    approval_verification_id: Sha256
    approval_public_key_sha256: Sha256
    approval_signature_envelope_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    execution_mode: Literal["evidence_only"] = "evidence_only"
    operator_action_required: Literal[True] = True
    targets: tuple[CleanupPreflightTargetResult, ...] = Field(
        min_length=1,
        max_length=2_048,
    )
    gates: tuple[CleanupPreflightGateResult, ...]
    ready_for_operator_action: bool

    @model_validator(mode="after")
    def identity_timing_and_verdict_match(self) -> Self:
        if not (
            self.action_capture_start_ts_ns
            <= self.action_capture_end_ts_ns
            <= self.evaluated_ts_ns
            < self.valid_until_ts_ns
        ):
            raise ValueError("cleanup preflight capture or validity interval is invalid")
        if self.valid_until_ts_ns != min(
            self.action_state_expires_ts_ns,
            self.approval_expires_ts_ns,
        ):
            raise ValueError("cleanup preflight validity must use the earliest expiry")
        target_ids = [item.target_id for item in self.targets]
        target_locators = [(item.kind, item.locator) for item in self.targets]
        if len(target_ids) != len(set(target_ids)) or len(target_locators) != len(
            set(target_locators)
        ):
            raise ValueError("cleanup preflight targets must be unique")

        names = [item.gate for item in self.gates]
        if len(names) != len(CleanupPreflightGate) or set(names) != set(CleanupPreflightGate):
            raise ValueError("cleanup preflight must contain every gate exactly once")
        expected_verdict = all(item.passed for item in self.gates) and all(
            item.state_matches for item in self.targets
        )
        if self.ready_for_operator_action != expected_verdict:
            raise ValueError("cleanup preflight verdict does not match its gates and targets")

        identity = self.model_dump(
            mode="json",
            exclude={"receipt_id", "ready_for_operator_action"},
        )
        if canonical_sha256(identity) != self.receipt_id:
            raise ValueError("cleanup preflight receipt identity does not match")
        return self


class CleanupActionStage(StrEnum):
    CREDENTIAL_REVOCATION = "credential_revocation"
    RUNTIME_RETIREMENT = "runtime_retirement"
    HOST_INTEGRATION_REMOVAL = "host_integration_removal"
    HOST_PACKAGE_REMOVAL = "host_package_removal"
    NATIVE_REPOSITORY_MIGRATION = "native_repository_migration"
    REPOSITORY_RETIREMENT = "repository_retirement"


_CLEANUP_ACTION_STAGE_ORDER = {
    stage: index for index, stage in enumerate(CleanupActionStage, start=1)
}


class CleanupActionOutcomeKind(StrEnum):
    REVOKED_SECRET = "revoked_secret"
    REMOVED_PATH = "removed_path"
    REMOVED_HOST_DEPENDENCY = "removed_host_dependency"
    NATIVE_MIGRATION = "native_migration"
    ARCHIVE_ONLY = "archive_only"


class CleanupActionEvidenceRequirement(StrEnum):
    APPROVED_PRE_ACTION_STATE = "approved_pre_action_state"
    ACTION_START_INSIDE_AUTHORITY = "action_start_inside_authority"
    PATH_ABSENCE = "path_absence"
    HOST_ABSENCE_DUAL_SOURCE = "host_absence_dual_source"
    PROVIDER_RECORD_REVOCATION = "provider_record_revocation"
    ZERO_ACTIVE_SESSIONS = "zero_active_sessions"
    SOURCE_PATH_ABSENCE = "source_path_absence"
    DESTINATION_PATH_INVENTORY = "destination_path_inventory"
    MIGRATION_COMMIT = "migration_commit"
    OPERATIONAL_COPY_ABSENCE = "operational_copy_absence"
    FINAL_ARCHIVE_BINDING = "final_archive_binding"
    RAW_EVIDENCE_AFTER_ACTION = "raw_evidence_after_action"
    INDEPENDENT_REVIEW = "independent_review"
    ZERO_FINDING_CREDENTIAL_SCAN = "zero_finding_credential_scan"


def cleanup_action_stage_for(target: LegacyCleanupTarget) -> CleanupActionStage:
    if target.action is CleanupAction.REVOKE:
        return CleanupActionStage.CREDENTIAL_REVOCATION
    if target.kind is CleanupTargetKind.RUNTIME_PATH:
        return CleanupActionStage.RUNTIME_RETIREMENT
    if target.kind is CleanupTargetKind.HOST_INTEGRATION:
        return CleanupActionStage.HOST_INTEGRATION_REMOVAL
    if target.kind is CleanupTargetKind.HOST_PACKAGE:
        return CleanupActionStage.HOST_PACKAGE_REMOVAL
    if target.action is CleanupAction.MIGRATE_NATIVE:
        return CleanupActionStage.NATIVE_REPOSITORY_MIGRATION
    return CleanupActionStage.REPOSITORY_RETIREMENT


def cleanup_action_outcome_for(target: LegacyCleanupTarget) -> CleanupActionOutcomeKind:
    if target.action is CleanupAction.REVOKE:
        return CleanupActionOutcomeKind.REVOKED_SECRET
    if target.action is CleanupAction.MIGRATE_NATIVE:
        return CleanupActionOutcomeKind.NATIVE_MIGRATION
    if target.action is CleanupAction.RETAIN_ARCHIVE_ONLY:
        return CleanupActionOutcomeKind.ARCHIVE_ONLY
    if target.kind in {CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH}:
        return CleanupActionOutcomeKind.REMOVED_PATH
    return CleanupActionOutcomeKind.REMOVED_HOST_DEPENDENCY


def cleanup_action_evidence_for(
    target: LegacyCleanupTarget,
) -> tuple[CleanupActionEvidenceRequirement, ...]:
    specific: tuple[CleanupActionEvidenceRequirement, ...]
    if target.action is CleanupAction.REVOKE:
        specific = (
            CleanupActionEvidenceRequirement.PROVIDER_RECORD_REVOCATION,
            CleanupActionEvidenceRequirement.ZERO_ACTIVE_SESSIONS,
        )
    elif target.action is CleanupAction.MIGRATE_NATIVE:
        specific = (
            CleanupActionEvidenceRequirement.SOURCE_PATH_ABSENCE,
            CleanupActionEvidenceRequirement.DESTINATION_PATH_INVENTORY,
            CleanupActionEvidenceRequirement.MIGRATION_COMMIT,
        )
    elif target.action is CleanupAction.RETAIN_ARCHIVE_ONLY:
        specific = (
            CleanupActionEvidenceRequirement.OPERATIONAL_COPY_ABSENCE,
            CleanupActionEvidenceRequirement.FINAL_ARCHIVE_BINDING,
        )
    elif target.kind in {CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH}:
        specific = (CleanupActionEvidenceRequirement.PATH_ABSENCE,)
    else:
        specific = (CleanupActionEvidenceRequirement.HOST_ABSENCE_DUAL_SOURCE,)
    return (
        CleanupActionEvidenceRequirement.APPROVED_PRE_ACTION_STATE,
        CleanupActionEvidenceRequirement.ACTION_START_INSIDE_AUTHORITY,
        *specific,
        CleanupActionEvidenceRequirement.RAW_EVIDENCE_AFTER_ACTION,
        CleanupActionEvidenceRequirement.INDEPENDENT_REVIEW,
        CleanupActionEvidenceRequirement.ZERO_FINDING_CREDENTIAL_SCAN,
    )


class CleanupActionPlanStep(DomainModel):
    step_id: Sha256
    sequence: int = Field(gt=0, le=2_048)
    stage: CleanupActionStage
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    expected_state_sha256: Sha256
    required_outcome: CleanupActionOutcomeKind
    evidence_requirements: tuple[CleanupActionEvidenceRequirement, ...] = Field(
        min_length=6,
        max_length=8,
    )
    manual_action_required: Literal[True] = True

    @model_validator(mode="after")
    def identity_and_typed_contract_match(self) -> Self:
        target = LegacyCleanupTarget(
            target_id=self.target_id,
            kind=self.kind,
            locator=self.locator,
            action=self.action,
            destination_locator=self.destination_locator,
            expected_state_sha256=self.expected_state_sha256,
            rationale="canonical cleanup action plan step",
        )
        if self.stage is not cleanup_action_stage_for(target):
            raise ValueError("cleanup action plan step stage does not match its target")
        if self.required_outcome is not cleanup_action_outcome_for(target):
            raise ValueError("cleanup action plan step outcome does not match its target")
        if self.evidence_requirements != cleanup_action_evidence_for(target):
            raise ValueError("cleanup action plan step evidence requirements are not exact")
        identity = self.model_dump(mode="json", exclude={"step_id"})
        if canonical_sha256(identity) != self.step_id:
            raise ValueError("cleanup action plan step identity does not match")
        return self


class CleanupActionPlan(DomainModel):
    """Canonical manual-action order; it carries no action implementation."""

    schema_version: Literal[1] = 1
    plan_id: Sha256
    retirement_id: Identifier
    prepared_ts_ns: int = Field(ge=0)
    preflight_evaluated_ts_ns: int = Field(ge=0)
    valid_until_ts_ns: int = Field(gt=0)
    policy_id: Identifier
    policy_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    archive_manifest_sha256: Sha256
    approved_cleanup_manifest_sha256: Sha256
    preflight_receipt_sha256: Sha256
    cleanup_approval_sha256: Sha256
    approval_verification_id: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    source_commit_sha: GitCommit
    final_tag_name: Literal["mt5-final"] = "mt5-final"
    execution_mode: Literal["evidence_only"] = "evidence_only"
    commands_included: Literal[False] = False
    operator_action_required: Literal[True] = True
    operator_ledger_required: Literal[True] = True
    steps: tuple[CleanupActionPlanStep, ...] = Field(min_length=1, max_length=2_048)
    ready_for_manual_action: Literal[True] = True

    @model_validator(mode="after")
    def identity_timing_and_order_match(self) -> Self:
        if not (self.preflight_evaluated_ts_ns <= self.prepared_ts_ns < self.valid_until_ts_ns):
            raise ValueError("cleanup action plan is outside its preflight validity window")
        sequences = tuple(item.sequence for item in self.steps)
        if sequences != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("cleanup action plan step sequence must be contiguous")
        target_ids = tuple(item.target_id for item in self.steps)
        locators = tuple((item.kind, item.locator) for item in self.steps)
        if len(target_ids) != len(set(target_ids)) or len(locators) != len(set(locators)):
            raise ValueError("cleanup action plan targets must be unique")
        stage_order = tuple(_CLEANUP_ACTION_STAGE_ORDER[item.stage] for item in self.steps)
        if stage_order != tuple(sorted(stage_order)):
            raise ValueError("cleanup action plan stages must follow the safe order")
        identity = self.model_dump(mode="json", exclude={"plan_id"})
        if canonical_sha256(identity) != self.plan_id:
            raise ValueError("cleanup action plan identity does not match")
        return self


class CleanupRemovedPathResult(DomainModel):
    result_kind: Literal["removed_path"] = "removed_path"
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    exists: Literal[False] = False
    observed_commit_sha: GitCommit | None = None
    raw_artifact_id: Identifier

    @model_validator(mode="after")
    def repository_result_is_commit_bound(self) -> Self:
        if (self.kind is CleanupTargetKind.REPOSITORY_PATH) != (
            self.observed_commit_sha is not None
        ):
            raise ValueError("only removed repository paths bind an observed commit")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return (self.raw_artifact_id,)


class CleanupPathAbsenceEvidence(DomainModel):
    schema_version: Literal[1] = 1
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    exists: Literal[False] = False
    observed_commit_sha: GitCommit | None = None
    captured_ts_ns: int = Field(ge=0)
    observation_source: Identifier

    @model_validator(mode="after")
    def repository_evidence_is_commit_bound(self) -> Self:
        if (self.kind is CleanupTargetKind.REPOSITORY_PATH) != (
            self.observed_commit_sha is not None
        ):
            raise ValueError("only repository absence evidence binds an observed commit")
        return self


class CleanupRemovedHostResult(DomainModel):
    result_kind: Literal["removed_host"] = "removed_host"
    kind: Literal[CleanupTargetKind.HOST_INTEGRATION, CleanupTargetKind.HOST_PACKAGE]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    installed: Literal[False] = False
    raw_artifact_ids: tuple[Identifier, Identifier]

    @model_validator(mode="after")
    def raw_artifacts_are_distinct(self) -> Self:
        if len(set(self.raw_artifact_ids)) != 2:
            raise ValueError("removed host result requires two distinct proofs")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return self.raw_artifact_ids


class CleanupHostAbsenceEvidence(DomainModel):
    schema_version: Literal[1] = 1
    kind: Literal[CleanupTargetKind.HOST_INTEGRATION, CleanupTargetKind.HOST_PACKAGE]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    installed: Literal[False] = False
    captured_ts_ns: int = Field(ge=0)
    observation_source: Identifier


class CleanupRevokedSecretResult(DomainModel):
    result_kind: Literal["revoked_secret"] = "revoked_secret"
    kind: Literal[CleanupTargetKind.SECRET_REFERENCE] = CleanupTargetKind.SECRET_REFERENCE
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    provider: Identifier
    provider_record_id_sha256: Sha256
    revoked: Literal[True] = True
    active_session_count: Literal[0] = 0
    provider_state_sha256: Sha256
    active_sessions_sha256: Sha256
    secret_material_included: Literal[False] = False
    raw_artifact_ids: tuple[Identifier, Identifier]

    @model_validator(mode="after")
    def raw_artifacts_are_distinct(self) -> Self:
        if len(set(self.raw_artifact_ids)) != 2:
            raise ValueError("revoked secret result requires distinct provider and session proofs")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return self.raw_artifact_ids


class CleanupNativeMigrationResult(DomainModel):
    result_kind: Literal["native_migration"] = "native_migration"
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH] = CleanupTargetKind.REPOSITORY_PATH
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)]
    source_exists: Literal[False] = False
    destination_exists: Literal[True] = True
    migration_commit_sha: GitCommit
    destination_inventory_sha256: Sha256
    raw_artifact_ids: tuple[Identifier, Identifier]

    @model_validator(mode="after")
    def paths_and_artifacts_are_distinct(self) -> Self:
        if self.locator == self.destination_locator:
            raise ValueError("native migration source and destination must differ")
        if len(set(self.raw_artifact_ids)) != 2:
            raise ValueError("native migration requires distinct source and destination proofs")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return self.raw_artifact_ids


class CleanupArchiveOnlyResult(DomainModel):
    result_kind: Literal["archive_only"] = "archive_only"
    kind: Literal[CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH]
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    exists: Literal[False] = False
    observed_commit_sha: GitCommit | None = None
    archive_manifest_sha256: Sha256
    raw_artifact_id: Identifier

    @model_validator(mode="after")
    def repository_result_is_commit_bound(self) -> Self:
        if (self.kind is CleanupTargetKind.REPOSITORY_PATH) != (
            self.observed_commit_sha is not None
        ):
            raise ValueError("only archived repository paths bind an observed commit")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return (self.raw_artifact_id,)


CleanupTargetOutcome = Annotated[
    CleanupRemovedPathResult
    | CleanupRemovedHostResult
    | CleanupRevokedSecretResult
    | CleanupNativeMigrationResult
    | CleanupArchiveOnlyResult,
    Field(discriminator="result_kind"),
]


class CleanupTargetOutcomeEvidence(DomainModel):
    schema_version: Literal[2] = 2
    retirement_id: Identifier
    plan_step_id: Sha256
    plan_sequence: int = Field(gt=0, le=2_048)
    plan_stage: CleanupActionStage
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    pre_action_state_sha256: Sha256
    action_started_ts_ns: int = Field(ge=0)
    action_completed_ts_ns: int = Field(ge=0)
    captured_ts_ns: int = Field(ge=0)
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    result: CleanupTargetOutcome
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def action_result_and_review_are_exact(self) -> Self:
        if not (self.action_started_ts_ns <= self.action_completed_ts_ns <= self.captured_ts_ns):
            raise ValueError("cleanup outcome action or capture interval is invalid")
        if self.collected_by == self.reviewed_by:
            raise ValueError("cleanup outcome requires an independent reviewer")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("cleanup outcome invalidating events must be unique")
        if self.invalidating_events:
            raise ValueError("cleanup outcome contains invalidating events")
        if self.result.kind is not self.kind or self.result.locator != self.locator:
            raise ValueError("cleanup outcome result target differs")
        expected_result_kind = {
            CleanupAction.MIGRATE_NATIVE: "native_migration",
            CleanupAction.REVOKE: "revoked_secret",
            CleanupAction.RETAIN_ARCHIVE_ONLY: "archive_only",
        }.get(self.action)
        if self.action is CleanupAction.REMOVE:
            expected_result_kind = (
                "removed_path"
                if self.kind in {CleanupTargetKind.REPOSITORY_PATH, CleanupTargetKind.RUNTIME_PATH}
                else "removed_host"
            )
        if self.result.result_kind != expected_result_kind:
            raise ValueError("cleanup action and outcome result kind differ")
        result_destination = getattr(self.result, "destination_locator", None)
        if result_destination != self.destination_locator:
            raise ValueError("cleanup outcome migration destination differs")
        return self

    def artifact_ids(self) -> tuple[str, ...]:
        return self.result.artifact_ids()

    def postcondition_sha256(self) -> str:
        return self.result.sha256()


class CleanupOutcomeControlKind(StrEnum):
    TARGET_OUTCOME = "target_outcome"
    CREDENTIAL_SCAN = "credential_scan"


class CleanupOutcomeControl(DomainModel):
    kind: CleanupOutcomeControlKind
    reference_id: Identifier
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
            raise ValueError("cleanup outcome controls must be below controls/ without traversal")
        return self


class CleanupOutcomeEvidenceManifest(DomainModel):
    schema_version: Literal[2] = 2
    retirement_id: Identifier
    created_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    credential_scan_policy_id: Identifier
    credential_scan_policy_sha256: Sha256
    source_commit_sha: GitCommit
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    cleanup_manifest_sha256: Sha256
    cleanup_preflight_receipt_sha256: Sha256
    cleanup_action_plan_sha256: Sha256
    contains_credentials: Literal[False] = False
    artifacts: tuple[CleanupEvidenceArtifact, ...] = Field(min_length=1, max_length=4_096)
    controls: tuple[CleanupOutcomeControl, ...] = Field(min_length=2, max_length=2_049)

    @model_validator(mode="after")
    def inventory_is_exact(self) -> Self:
        artifact_ids = [item.artifact_id for item in self.artifacts]
        paths = [item.relative_path for item in self.artifacts]
        control_refs = [(item.kind, item.reference_id) for item in self.controls]
        paths.extend(item.relative_path for item in self.controls)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("cleanup outcome artifact identities must be unique")
        if len(control_refs) != len(set(control_refs)):
            raise ValueError("cleanup outcome control references must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("cleanup outcome evidence paths must be unique")
        kinds = [item.kind for item in self.controls]
        if kinds.count(CleanupOutcomeControlKind.CREDENTIAL_SCAN) != 1:
            raise ValueError("cleanup outcome requires one credential scan")
        if CleanupOutcomeControlKind.TARGET_OUTCOME not in kinds:
            raise ValueError("cleanup outcome requires target controls")
        if any(item.captured_ts_ns > self.created_ts_ns for item in self.artifacts) or any(
            item.captured_ts_ns > self.created_ts_ns for item in self.controls
        ):
            raise ValueError("cleanup outcome evidence cannot postdate its manifest")
        return self


class CleanupOutcomeGate(StrEnum):
    PREFLIGHT_REPLAYED = "preflight_replayed"
    ACTION_PLAN_REPLAYED = "action_plan_replayed"
    PLAN_SEQUENCE_EXACT = "plan_sequence_exact"
    ACTIONS_STARTED_WHILE_AUTHORIZED = "actions_started_while_authorized"
    TARGET_INVENTORY_EXACT = "target_inventory_exact"
    PRE_ACTION_STATE_BOUND = "pre_action_state_bound"
    POSTCONDITIONS_VERIFIED = "postconditions_verified"
    OUTCOME_EVIDENCE_EXACT = "outcome_evidence_exact"
    CREDENTIAL_SCAN_PASSED = "credential_scan_passed"
    ARCHIVE_RETENTION_ACTIVE = "archive_retention_active"
    INDEPENDENT_REVIEW_COMPLETE = "independent_review_complete"


class CleanupOutcomeGateResult(DomainModel):
    gate: CleanupOutcomeGate
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=2_048)]
    required: Annotated[str, Field(min_length=1, max_length=2_048)]


class CleanupOutcomeTargetResult(DomainModel):
    plan_step_id: Sha256
    plan_sequence: int = Field(gt=0, le=2_048)
    plan_stage: CleanupActionStage
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    pre_action_state_sha256: Sha256
    postcondition_sha256: Sha256
    action_started_ts_ns: int = Field(ge=0)
    action_completed_ts_ns: int = Field(ge=0)
    postcondition_met: bool


class CleanupCompletionReport(DomainModel):
    """Immutable evidence verdict; it cannot perform or authorize cleanup."""

    schema_version: Literal[2] = 2
    report_id: Sha256
    retirement_id: Identifier
    generated_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    source_commit_sha: GitCommit
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    cleanup_manifest_sha256: Sha256
    cleanup_preflight_receipt_sha256: Sha256
    cleanup_action_plan_sha256: Sha256
    outcome_evidence_manifest_sha256: Sha256
    outcome_evidence_bundle_sha256: Sha256
    credential_scan_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    verification_mode: Literal["evidence_only"] = "evidence_only"
    operator_actions_observed: Literal[True] = True
    targets: tuple[CleanupOutcomeTargetResult, ...] = Field(min_length=1, max_length=2_048)
    gates: tuple[CleanupOutcomeGateResult, ...]
    cleanup_complete: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        target_ids = [item.target_id for item in self.targets]
        target_locators = [(item.kind, item.locator) for item in self.targets]
        if len(target_ids) != len(set(target_ids)) or len(target_locators) != len(
            set(target_locators)
        ):
            raise ValueError("cleanup completion targets must be unique")
        plan_step_ids = tuple(item.plan_step_id for item in self.targets)
        plan_sequences = tuple(item.plan_sequence for item in self.targets)
        if len(plan_step_ids) != len(set(plan_step_ids)) or plan_sequences != tuple(
            range(1, len(self.targets) + 1)
        ):
            raise ValueError("cleanup completion plan sequence must be unique and contiguous")
        names = [item.gate for item in self.gates]
        if len(names) != len(CleanupOutcomeGate) or set(names) != set(CleanupOutcomeGate):
            raise ValueError("cleanup completion must contain every gate exactly once")
        expected_verdict = all(item.passed for item in self.gates) and all(
            item.postcondition_met for item in self.targets
        )
        if self.cleanup_complete != expected_verdict:
            raise ValueError("cleanup completion verdict does not match gates and targets")
        identity = self.model_dump(mode="json", exclude={"report_id", "cleanup_complete"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("cleanup completion report identity does not match")
        return self


class CleanupOperatorLedgerEntry(DomainModel):
    sequence: int = Field(gt=0, le=2_048)
    plan_step_id: Sha256
    stage: CleanupActionStage
    target_id: Identifier
    kind: CleanupTargetKind
    locator: Annotated[str, Field(min_length=1, max_length=512)]
    action: CleanupAction
    destination_locator: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    expected_state_sha256: Sha256
    required_outcome: CleanupActionOutcomeKind
    evidence_requirements: tuple[CleanupActionEvidenceRequirement, ...] = Field(
        min_length=6,
        max_length=8,
    )
    action_started_ts_ns: int = Field(ge=0)
    action_completed_ts_ns: int = Field(ge=0)
    target_outcome_evidence_sha256: Sha256
    postcondition_sha256: Sha256
    collected_by: Annotated[str, Field(min_length=1, max_length=256)]
    reviewed_by: Annotated[str, Field(min_length=1, max_length=256)]
    status: Literal["verified_complete"] = "verified_complete"

    @model_validator(mode="after")
    def timing_and_review_are_valid(self) -> Self:
        if self.action_completed_ts_ns < self.action_started_ts_ns:
            raise ValueError("cleanup operator ledger action interval is reversed")
        if self.collected_by == self.reviewed_by:
            raise ValueError("cleanup operator ledger requires an independent reviewer")
        target = LegacyCleanupTarget(
            target_id=self.target_id,
            kind=self.kind,
            locator=self.locator,
            action=self.action,
            destination_locator=self.destination_locator,
            expected_state_sha256=self.expected_state_sha256,
            rationale="canonical cleanup operator ledger entry",
        )
        if self.stage is not cleanup_action_stage_for(target):
            raise ValueError("cleanup operator ledger stage does not match its target")
        if self.required_outcome is not cleanup_action_outcome_for(target):
            raise ValueError("cleanup operator ledger outcome does not match its target")
        if self.evidence_requirements != cleanup_action_evidence_for(target):
            raise ValueError("cleanup operator ledger evidence requirements are not exact")
        return self


class CleanupOperatorLedger(DomainModel):
    """Canonical closeout record derived from a complete evidence replay."""

    schema_version: Literal[1] = 1
    ledger_id: Sha256
    retirement_id: Identifier
    generated_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    source_commit_sha: GitCommit
    archive_manifest_sha256: Sha256
    disabled_observation_report_sha256: Sha256
    cleanup_manifest_sha256: Sha256
    cleanup_preflight_receipt_sha256: Sha256
    cleanup_action_plan_sha256: Sha256
    cleanup_approval_sha256: Sha256
    approval_verification_id: Sha256
    cleanup_completion_report_sha256: Sha256
    outcome_evidence_manifest_sha256: Sha256
    outcome_evidence_bundle_sha256: Sha256
    credential_scan_sha256: Sha256
    native_deployment_id: Identifier
    native_admission_id: Sha256
    verification_mode: Literal["evidence_only"] = "evidence_only"
    operator_actions_observed: Literal[True] = True
    entries: tuple[CleanupOperatorLedgerEntry, ...] = Field(min_length=1, max_length=2_048)
    closeout_complete: Literal[True] = True

    @model_validator(mode="after")
    def identity_order_and_timing_match(self) -> Self:
        sequences = tuple(item.sequence for item in self.entries)
        if sequences != tuple(range(1, len(self.entries) + 1)):
            raise ValueError("cleanup operator ledger sequence must be contiguous")
        step_ids = tuple(item.plan_step_id for item in self.entries)
        target_ids = tuple(item.target_id for item in self.entries)
        if len(step_ids) != len(set(step_ids)) or len(target_ids) != len(set(target_ids)):
            raise ValueError("cleanup operator ledger steps and targets must be unique")
        stage_order = tuple(_CLEANUP_ACTION_STAGE_ORDER[item.stage] for item in self.entries)
        if stage_order != tuple(sorted(stage_order)):
            raise ValueError("cleanup operator ledger stages must follow the safe order")
        if any(item.action_completed_ts_ns > self.generated_ts_ns for item in self.entries):
            raise ValueError("cleanup operator ledger cannot predate completed actions")
        identity = self.model_dump(mode="json", exclude={"ledger_id", "closeout_complete"})
        if canonical_sha256(identity) != self.ledger_id:
            raise ValueError("cleanup operator ledger identity does not match")
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
