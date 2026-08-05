"""Immutable assembly and replay of the reversible legacy-disabled window."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path, PurePosixPath
from time import time_ns

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.retirement.approval import (
    ExpectedRetirementAction,
    RetirementApprovalPaths,
    verify_retirement_approval,
)
from aiquanttrader_native.retirement.archive import verify_legacy_archive_manifest
from aiquanttrader_native.retirement.collector import verify_native_production_observation
from aiquanttrader_native.retirement.models import (
    DisabledCredentialScanEvidence,
    DisabledEvidenceArtifact,
    DisabledEvidenceArtifactKind,
    DisabledEvidenceControl,
    DisabledEvidenceControlKind,
    DisabledEvidenceManifest,
    DisabledObservation,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyBrokerOrderAuditEvidence,
    LegacyCapability,
    LegacyCapabilityAuditEvidence,
    LegacyCapabilityObservation,
    LegacyCredentialQuarantineEvidence,
    LegacyStopExecutionEvidence,
    NativeDisabledWindowEvidence,
    NativeProductionObservation,
    RequiredLegacyStopAction,
    RetirementActionScope,
    RetirementPolicy,
    RetirementReadinessObservation,
    RetirementReadinessReport,
    VerifiedRetirementApproval,
)

MANIFEST_NAME = "disabled-evidence.json"
MAX_CONTROL_BYTES = 16_777_216
MAX_BUNDLE_FILES = 8_199
MAX_BUNDLE_BYTES = 1_099_511_627_776


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    byte_count: int
    modified_ts_ns: int
    changed_ts_ns: int


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    relative_path: str
    content_sha256: str
    byte_count: int
    identity: FileIdentity


def assemble_disabled_observation(
    disabled_evidence_root: Path,
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    readiness_observation: RetirementReadinessObservation,
    readiness_report: RetirementReadinessReport,
    native_observation: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    *,
    stop_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_native_key_id: str,
    expected_native_public_key_sha256: str,
    expected_stop_key_id: str,
    expected_stop_public_key_sha256: str,
) -> DisabledObservation:
    """Assemble one observation without service, broker, wallet, or signer capability."""

    return _assemble_disabled_observation(
        disabled_evidence_root,
        native_evidence_root,
        legacy_evidence_root,
        readiness_observation,
        readiness_report,
        native_observation,
        archive_manifest,
        stop_approval_paths=stop_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_native_key_id=expected_native_key_id,
        expected_native_public_key_sha256=expected_native_public_key_sha256,
        expected_stop_key_id=expected_stop_key_id,
        expected_stop_public_key_sha256=expected_stop_public_key_sha256,
        assembled_ts_ns=time_ns(),
    )


def verify_disabled_observation(
    disabled_evidence_root: Path,
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    observation: DisabledObservation,
    readiness_observation: RetirementReadinessObservation,
    readiness_report: RetirementReadinessReport,
    native_observation: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    *,
    stop_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_native_key_id: str,
    expected_native_public_key_sha256: str,
    expected_stop_key_id: str,
    expected_stop_public_key_sha256: str,
) -> DisabledObservation:
    """Replay the exact evidence roots and external trust inputs for one observation."""

    verified_ts_ns = time_ns()
    if observation.assembled_ts_ns > verified_ts_ns:
        raise ValueError("disabled observation is dated after verification")
    assembled = _assemble_disabled_observation(
        disabled_evidence_root,
        native_evidence_root,
        legacy_evidence_root,
        readiness_observation,
        readiness_report,
        native_observation,
        archive_manifest,
        stop_approval_paths=stop_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_native_key_id=expected_native_key_id,
        expected_native_public_key_sha256=expected_native_public_key_sha256,
        expected_stop_key_id=expected_stop_key_id,
        expected_stop_public_key_sha256=expected_stop_public_key_sha256,
        assembled_ts_ns=observation.assembled_ts_ns,
    )
    if assembled != observation:
        raise ValueError("disabled observation does not match its evidence roots")
    return assembled


def load_disabled_observation(path: Path) -> DisabledObservation:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    observation = DisabledObservation.model_validate_json(payload)
    if payload != observation.canonical_bytes() + b"\n":
        raise ValueError("disabled observation is not canonical JSON")
    return observation


def load_retirement_readiness_report(path: Path) -> RetirementReadinessReport:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    report = RetirementReadinessReport.model_validate_json(payload)
    if payload != report.canonical_bytes() + b"\n":
        raise ValueError("retirement readiness report is not canonical JSON")
    return report


def _assemble_disabled_observation(
    disabled_evidence_root: Path,
    native_evidence_root: Path,
    legacy_evidence_root: Path,
    readiness_observation: RetirementReadinessObservation,
    readiness_report: RetirementReadinessReport,
    native_observation: NativeProductionObservation,
    archive_manifest: LegacyArchiveManifest,
    *,
    stop_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_native_key_id: str,
    expected_native_public_key_sha256: str,
    expected_stop_key_id: str,
    expected_stop_public_key_sha256: str,
    assembled_ts_ns: int,
) -> DisabledObservation:
    root = _validated_root(disabled_evidence_root)
    manifest = _load_control(root / MANIFEST_NAME, DisabledEvidenceManifest)
    if manifest.created_ts_ns > assembled_ts_ns or manifest.ended_ts_ns > assembled_ts_ns:
        raise ValueError("disabled evidence is dated after assembly")
    if policy.frozen_at_ns > manifest.started_ts_ns:
        raise ValueError("retirement policy was not frozen before disabled observation")

    bindings: dict[str, DisabledEvidenceArtifact | DisabledEvidenceControl] = {
        item.relative_path: item for item in manifest.artifacts
    }
    bindings.update({item.relative_path: item for item in manifest.controls})
    inventory = _validate_inventory(root, bindings)
    inventory_by_path = {item.relative_path: item for item in inventory}
    expected_manifest_sha256 = hashlib.sha256(manifest.canonical_bytes() + b"\n").hexdigest()
    if inventory_by_path[MANIFEST_NAME].content_sha256 != expected_manifest_sha256:
        raise ValueError("disabled evidence manifest changed while loading")

    controls = {item.kind: item for item in manifest.controls}
    stop = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.STOP_EXECUTION],
        LegacyStopExecutionEvidence,
    )
    capability_audit = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.CAPABILITY_AUDIT],
        LegacyCapabilityAuditEvidence,
    )
    broker_audit = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.BROKER_ORDER_AUDIT],
        LegacyBrokerOrderAuditEvidence,
    )
    credential_evidence = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.CREDENTIAL_QUARANTINE],
        LegacyCredentialQuarantineEvidence,
    )
    native_stability = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.NATIVE_STABILITY_AUDIT],
        NativeDisabledWindowEvidence,
    )
    credential_scan = _load_bound_control(
        root,
        controls[DisabledEvidenceControlKind.CREDENTIAL_SCAN],
        DisabledCredentialScanEvidence,
    )
    artifacts = {item.relative_path: item for item in manifest.artifacts}

    _verify_manifest_lineage(
        manifest,
        stop,
        capability_audit,
        broker_audit,
        credential_evidence,
        native_stability,
        credential_scan,
        controls,
    )
    _verify_external_lineage(
        manifest,
        stop,
        readiness_observation,
        readiness_report,
        archive_manifest,
        policy,
    )
    verified_stop_approval = _verify_stop_approval(
        stop,
        readiness_report,
        stop_approval_paths,
        expected_key_id=expected_stop_key_id,
        expected_public_key_sha256=expected_stop_public_key_sha256,
    )
    verified_native = verify_native_production_observation(
        native_evidence_root,
        native_observation,
        policy=policy,
        expected_key_id=expected_native_key_id,
        expected_public_key_sha256=expected_native_public_key_sha256,
    )
    verified_archive = verify_legacy_archive_manifest(
        legacy_evidence_root,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
    )
    _verify_native_and_archive_lineage(
        manifest,
        readiness_observation,
        readiness_report,
        verified_native,
        verified_archive,
        policy,
    )
    _verify_raw_references(
        artifacts,
        stop,
        capability_audit,
        broker_audit,
        credential_evidence,
        native_stability,
    )
    _verify_credential_scan(
        manifest,
        artifacts,
        credential_scan,
        credential_scan_policy,
    )
    _assert_inventory_unchanged(root, inventory)

    bundle_sha256 = canonical_sha256(
        {
            "schema_version": 1,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "content_sha256": item.content_sha256,
                    "byte_count": item.byte_count,
                }
                for item in inventory
            ],
        }
    )
    capability_summary, sample_count, maximum_gap_ns = _summarize_capabilities(
        capability_audit,
        artifacts,
    )
    mt5_stopped_ts_ns = next(
        item.completed_ts_ns
        for item in stop.actions
        if item.action is RequiredLegacyStopAction.STOP_MT5
    )
    broker_complete = (
        broker_audit.coverage_complete
        and broker_audit.queried_start_ts_ns <= mt5_stopped_ts_ns
        and broker_audit.queried_end_ts_ns >= manifest.ended_ts_ns
        and broker_audit.account_login_sha256 == readiness_observation.legacy.account_login_sha256
        and broker_audit.broker_server_sha256 == readiness_observation.legacy.broker_server_sha256
    )
    post_stop_orders = sum(item.created_ts_ns >= mt5_stopped_ts_ns for item in broker_audit.orders)
    credentials_quarantined = (
        credential_evidence.started_ts_ns <= manifest.started_ts_ns
        and credential_evidence.observed_through_ts_ns >= manifest.ended_ts_ns
        and credential_evidence.inventory_complete
        and credential_evidence.continuous_audit
        and all(
            item.quarantined and item.active_reader_count == 0
            for item in credential_evidence.checks
        )
    )
    return DisabledObservation(
        retirement_id=manifest.retirement_id,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        assembled_ts_ns=assembled_ts_ns,
        readiness_report_sha256=readiness_report.sha256(),
        stop_approval_sha256=verified_stop_approval.approval.sha256(),
        stop_approval_verification_id=verified_stop_approval.verification_id,
        archive_manifest_sha256=verified_archive.sha256(),
        archive_bundle_sha256=verified_archive.evidence_bundle_sha256,
        native_deployment_id=verified_native.deployment_id,
        native_admission_id=verified_native.admission_id,
        native_observation_sha256=verified_native.sha256(),
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
        capability_sample_count=sample_count,
        maximum_capability_gap_ns=maximum_gap_ns,
        capability_audit_invalidating_events=len(capability_audit.invalidating_events),
        native_critical_incidents=native_stability.critical_incidents,
        native_reconciliation_failures=native_stability.reconciliation_failures,
        native_risk_breaches=native_stability.risk_breaches,
        native_audit_complete=native_stability.continuous_monitoring,
        legacy_broker_orders_after_stop=post_stop_orders,
        legacy_broker_order_audit_complete=broker_complete,
        archive_reverified=True,
        legacy_credentials_quarantined=credentials_quarantined,
        capabilities=capability_summary,
        evidence_manifest_sha256=manifest.sha256(),
        credential_scan_sha256=credential_scan.sha256(),
        evidence_bundle_sha256=bundle_sha256,
    )


def _verify_manifest_lineage(
    manifest: DisabledEvidenceManifest,
    stop: LegacyStopExecutionEvidence,
    capability_audit: LegacyCapabilityAuditEvidence,
    broker_audit: LegacyBrokerOrderAuditEvidence,
    credential_evidence: LegacyCredentialQuarantineEvidence,
    native_stability: NativeDisabledWindowEvidence,
    credential_scan: DisabledCredentialScanEvidence,
    controls: dict[DisabledEvidenceControlKind, DisabledEvidenceControl],
) -> None:
    identities = {
        stop.retirement_id,
        capability_audit.retirement_id,
        broker_audit.retirement_id,
        credential_evidence.retirement_id,
        native_stability.retirement_id,
        credential_scan.retirement_id,
    }
    if identities != {manifest.retirement_id}:
        raise ValueError("disabled evidence retirement identities differ")
    if (
        native_stability.native_deployment_id != manifest.native_deployment_id
        or native_stability.native_admission_id != manifest.native_admission_id
    ):
        raise ValueError("native disabled-window audit identity differs from manifest")
    if (
        stop.ended_ts_ns != manifest.started_ts_ns
        or capability_audit.started_ts_ns != manifest.started_ts_ns
        or capability_audit.ended_ts_ns != manifest.ended_ts_ns
        or broker_audit.queried_end_ts_ns < manifest.ended_ts_ns
        or credential_evidence.started_ts_ns > manifest.started_ts_ns
        or credential_evidence.observed_through_ts_ns < manifest.ended_ts_ns
        or native_stability.started_ts_ns != manifest.started_ts_ns
        or native_stability.ended_ts_ns != manifest.ended_ts_ns
    ):
        raise ValueError("disabled evidence controls do not cover the declared interval")
    reviewed_or_ended = {
        DisabledEvidenceControlKind.STOP_EXECUTION: stop.ended_ts_ns,
        DisabledEvidenceControlKind.CAPABILITY_AUDIT: capability_audit.reviewed_ts_ns,
        DisabledEvidenceControlKind.BROKER_ORDER_AUDIT: broker_audit.reviewed_ts_ns,
        DisabledEvidenceControlKind.CREDENTIAL_QUARANTINE: credential_evidence.reviewed_ts_ns,
        DisabledEvidenceControlKind.NATIVE_STABILITY_AUDIT: native_stability.reviewed_ts_ns,
        DisabledEvidenceControlKind.CREDENTIAL_SCAN: credential_scan.reviewed_ts_ns,
    }
    for kind, timestamp in reviewed_or_ended.items():
        if controls[kind].captured_ts_ns < timestamp:
            raise ValueError(f"disabled evidence control predates its review: {kind.value}")


def _verify_credential_scan(
    manifest: DisabledEvidenceManifest,
    artifacts: dict[str, DisabledEvidenceArtifact],
    credential_scan: DisabledCredentialScanEvidence,
    policy: LegacyArchiveCredentialScanPolicy,
) -> None:
    if (
        credential_scan.policy_id != policy.policy_id
        or credential_scan.policy_sha256 != policy.sha256()
    ):
        raise ValueError("disabled credential scan used a different frozen policy")
    if credential_scan.started_ts_ns < max(item.captured_end_ts_ns for item in artifacts.values()):
        raise ValueError("disabled credential scan began before raw evidence collection ended")
    if credential_scan.reviewed_ts_ns > manifest.created_ts_ns:
        raise ValueError("disabled credential scan review postdates its manifest")
    checks = {item.artifact_id: item for item in credential_scan.checks}
    by_id = {item.artifact_id: item for item in artifacts.values()}
    if set(checks) != set(by_id):
        raise ValueError("disabled credential scan artifact inventory is not exact")
    for artifact_id, artifact in by_id.items():
        if checks[artifact_id].artifact_sha256 != artifact.content_sha256:
            raise ValueError(f"disabled credential scan hash differs: {artifact_id}")


def _verify_external_lineage(
    manifest: DisabledEvidenceManifest,
    stop: LegacyStopExecutionEvidence,
    readiness_observation: RetirementReadinessObservation,
    readiness_report: RetirementReadinessReport,
    archive_manifest: LegacyArchiveManifest,
    policy: RetirementPolicy,
) -> None:
    if not readiness_report.awaiting_stop_approval:
        raise ValueError("disabled evidence requires a passing retirement readiness report")
    if (
        readiness_report.policy_id != policy.policy_id
        or readiness_report.policy_sha256 != policy.sha256()
    ):
        raise ValueError("retirement readiness report used a different frozen policy")
    if readiness_observation.sha256() != readiness_report.observation_sha256:
        raise ValueError("retirement readiness observation differs from its report")
    if readiness_observation.archive != archive_manifest:
        raise ValueError("retirement readiness observation differs from retained archive")
    if (
        manifest.readiness_report_sha256 != readiness_report.sha256()
        or stop.readiness_report_sha256 != readiness_report.sha256()
        or manifest.stop_approval_sha256 != stop.stop_approval_sha256
        or manifest.archive_manifest_sha256 != archive_manifest.sha256()
        or manifest.native_deployment_id != readiness_report.native_deployment_id
        or manifest.native_admission_id != readiness_report.native_admission_id
        or manifest.retirement_id != readiness_report.retirement_id
    ):
        raise ValueError("disabled evidence manifest differs from readiness lineage")
    if stop.started_ts_ns < readiness_report.generated_ts_ns:
        raise ValueError("legacy stop predates the retirement readiness report")


def _verify_stop_approval(
    stop: LegacyStopExecutionEvidence,
    readiness_report: RetirementReadinessReport,
    paths: RetirementApprovalPaths,
    *,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> VerifiedRetirementApproval:
    completion = _datetime_from_ns(stop.ended_ts_ns)
    verified = verify_retirement_approval(
        paths=paths,
        expected=ExpectedRetirementAction(
            retirement_id=readiness_report.retirement_id,
            scope=RetirementActionScope.STOP_AND_OBSERVE,
            report_sha256=readiness_report.sha256(),
            native_deployment_id=readiness_report.native_deployment_id,
            native_admission_id=readiness_report.native_admission_id,
            archive_manifest_sha256=readiness_report.archive_manifest_sha256,
            source_commit_sha=readiness_report.source_commit_sha,
        ),
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
        now=completion,
    )
    if verified.approval.sha256() != stop.stop_approval_sha256:
        raise ValueError("legacy stop execution binds a different stop approval")
    if _datetime_ns(verified.approval.approved_at) > stop.started_ts_ns:
        raise ValueError("legacy stop execution began before approval")
    return verified


def _verify_native_and_archive_lineage(
    manifest: DisabledEvidenceManifest,
    readiness_observation: RetirementReadinessObservation,
    readiness_report: RetirementReadinessReport,
    native: NativeProductionObservation,
    archive: LegacyArchiveManifest,
    policy: RetirementPolicy,
) -> None:
    if (
        native.retirement_id != manifest.retirement_id
        or native.deployment_id != manifest.native_deployment_id
        or native.admission_id != manifest.native_admission_id
        or native.deployment_id != readiness_report.native_deployment_id
        or native.admission_id != readiness_report.native_admission_id
        or native.policy_id != policy.policy_id
        or native.policy_sha256 != policy.sha256()
    ):
        raise ValueError("native disabled-window evidence differs from readiness identity")
    if (
        archive.retirement_id != manifest.retirement_id
        or archive.sha256() != manifest.archive_manifest_sha256
        or archive.sha256() != readiness_report.archive_manifest_sha256
        or archive != readiness_observation.archive
    ):
        raise ValueError("reverified archive differs from disabled-window lineage")


def _verify_raw_references(
    artifacts: dict[str, DisabledEvidenceArtifact],
    stop: LegacyStopExecutionEvidence,
    capability_audit: LegacyCapabilityAuditEvidence,
    broker_audit: LegacyBrokerOrderAuditEvidence,
    credential_evidence: LegacyCredentialQuarantineEvidence,
    native_stability: NativeDisabledWindowEvidence,
) -> None:
    referenced: set[str] = set()
    for action in stop.actions:
        artifact = _require_artifact(
            artifacts,
            action.evidence_path,
            DisabledEvidenceArtifactKind.STOP_EXECUTION_OUTPUT,
        )
        if not (
            artifact.captured_start_ts_ns <= action.completed_ts_ns <= artifact.captured_end_ts_ns
        ):
            raise ValueError("legacy stop action timestamp is outside its raw evidence")
        referenced.add(action.evidence_path)
    for sample in capability_audit.samples:
        artifact = _require_artifact(
            artifacts,
            sample.evidence_path,
            DisabledEvidenceArtifactKind.CAPABILITY_SNAPSHOT,
        )
        if not (
            artifact.captured_start_ts_ns <= sample.observed_ts_ns <= artifact.captured_end_ts_ns
        ):
            raise ValueError("legacy capability sample timestamp is outside its raw evidence")
        referenced.add(sample.evidence_path)
    broker_artifact = _require_artifact(
        artifacts,
        broker_audit.source_evidence_path,
        DisabledEvidenceArtifactKind.BROKER_ORDER_EXPORT,
    )
    if (
        broker_artifact.captured_start_ts_ns > broker_audit.queried_start_ts_ns
        or broker_artifact.captured_end_ts_ns < broker_audit.queried_end_ts_ns
    ):
        raise ValueError("legacy broker export does not cover its audited interval")
    referenced.add(broker_audit.source_evidence_path)
    for check in credential_evidence.checks:
        artifact = _require_artifact(
            artifacts,
            check.evidence_path,
            DisabledEvidenceArtifactKind.CREDENTIAL_QUARANTINE_AUDIT,
        )
        if (
            artifact.captured_start_ts_ns > credential_evidence.started_ts_ns
            or artifact.captured_end_ts_ns < credential_evidence.observed_through_ts_ns
        ):
            raise ValueError("credential audit artifact does not cover the quarantine interval")
        referenced.add(check.evidence_path)
    for path in native_stability.evidence_paths:
        artifact = _require_artifact(
            artifacts,
            path,
            DisabledEvidenceArtifactKind.NATIVE_OPERATIONAL_AUDIT,
        )
        if (
            artifact.captured_start_ts_ns > native_stability.started_ts_ns
            or artifact.captured_end_ts_ns < native_stability.ended_ts_ns
        ):
            raise ValueError("native operational evidence does not cover the disabled window")
        referenced.add(path)
    if referenced != set(artifacts):
        raise ValueError("disabled evidence contains unreferenced or missing raw artifacts")


def _summarize_capabilities(
    audit: LegacyCapabilityAuditEvidence,
    artifacts: dict[str, DisabledEvidenceArtifact],
) -> tuple[tuple[LegacyCapabilityObservation, ...], int, int]:
    timestamps = [sample.observed_ts_ns for sample in audit.samples]
    gaps = [
        timestamps[0] - audit.started_ts_ns,
        *(right - left for left, right in pairwise(timestamps)),
        audit.ended_ts_ns - timestamps[-1],
    ]
    summaries: list[LegacyCapabilityObservation] = []
    for capability in LegacyCapability:
        states = [
            next(state for state in sample.states if state.capability is capability)
            for sample in audit.samples
        ]
        evidence_hashes = [
            artifacts[sample.evidence_path].content_sha256 for sample in audit.samples
        ]
        summaries.append(
            LegacyCapabilityObservation(
                capability=capability,
                disabled=all(state.disabled for state in states),
                active_instance_count=max(state.active_instance_count for state in states),
                evidence_sha256=canonical_sha256(
                    {
                        "schema_version": 1,
                        "capability": capability.value,
                        "evidence_sha256": evidence_hashes,
                    }
                ),
            )
        )
    return tuple(summaries), len(audit.samples), max(gaps)


def _require_artifact(
    artifacts: dict[str, DisabledEvidenceArtifact],
    path: str,
    kind: DisabledEvidenceArtifactKind,
) -> DisabledEvidenceArtifact:
    artifact = artifacts.get(path)
    if artifact is None:
        raise ValueError(f"disabled control references unbound raw evidence: {path}")
    if artifact.kind is not kind:
        raise ValueError(f"disabled raw evidence category differs: {path}")
    return artifact


def _validated_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("disabled evidence root must be absolute")
    if root.is_symlink():
        raise ValueError("disabled evidence root must be a non-symlink directory")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("disabled evidence root must be a non-symlink directory")
    return resolved


def _validate_inventory(
    root: Path,
    bindings: dict[str, DisabledEvidenceArtifact | DisabledEvidenceControl],
) -> tuple[InventoryEntry, ...]:
    expected = {MANIFEST_NAME, *bindings}
    expected_directories = {
        parent.as_posix()
        for value in expected
        for parent in PurePosixPath(value).parents
        if parent.as_posix() != "."
    }
    actual: dict[str, Path] = {}
    actual_directories: set[str] = set()
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"disabled evidence cannot contain symlinks: {path.name}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"disabled evidence contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"disabled evidence is group/world writable: {path.name}")
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("disabled evidence exceeds its hard resource bounds")
    if set(actual) != expected or actual_directories != expected_directories:
        raise ValueError("disabled evidence inventory is not exact")

    inventory: list[InventoryEntry] = []
    for relative_path, path in sorted(actual.items()):
        digest, identity = _hash_regular(path)
        binding = bindings.get(relative_path)
        if binding is not None and (
            binding.content_sha256 != digest or binding.byte_count != identity.byte_count
        ):
            raise ValueError(f"disabled evidence digest or size differs: {relative_path}")
        inventory.append(
            InventoryEntry(
                relative_path=relative_path,
                content_sha256=digest,
                byte_count=identity.byte_count,
                identity=identity,
            )
        )
    return tuple(inventory)


def _load_bound_control[ModelT: DomainModel](
    root: Path,
    binding: DisabledEvidenceControl,
    model: type[ModelT],
) -> ModelT:
    value = _load_control(root / binding.relative_path, model)
    if hashlib.sha256(value.canonical_bytes() + b"\n").hexdigest() != binding.content_sha256:
        raise ValueError(f"disabled control hash differs: {binding.kind.value}")
    return value


def _load_control[ModelT: DomainModel](path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"disabled evidence control is not canonical JSON: {path.name}")
    return value


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open disabled evidence artifact: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("disabled evidence artifact must be a regular file")
        if initial.st_size <= 0 or initial.st_size > maximum_bytes:
            raise ValueError("disabled evidence artifact size is invalid")
        payload = bytearray()
        remaining = initial.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        if len(payload) != initial.st_size or _identity(final) != _identity(initial):
            raise ValueError("disabled evidence artifact changed while read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _hash_regular(path: Path) -> tuple[str, FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open disabled evidence artifact: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("disabled evidence artifact must be a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            digest.update(chunk)
        final = os.fstat(descriptor)
        if _identity(final) != _identity(initial):
            raise ValueError("disabled evidence artifact changed while hashing")
        return digest.hexdigest(), _file_identity(final)
    finally:
        os.close(descriptor)


def _assert_inventory_unchanged(root: Path, inventory: tuple[InventoryEntry, ...]) -> None:
    for item in inventory:
        digest, identity = _hash_regular(root / item.relative_path)
        if digest != item.content_sha256 or identity != item.identity:
            raise ValueError("disabled evidence changed during verification")


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        byte_count=metadata.st_size,
        modified_ts_ns=metadata.st_mtime_ns,
        changed_ts_ns=metadata.st_ctime_ns,
    )


def _datetime_from_ns(timestamp_ns: int) -> datetime:
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=nanoseconds // 1_000)


def _datetime_ns(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1_000
