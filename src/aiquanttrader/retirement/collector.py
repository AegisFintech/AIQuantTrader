"""Credential-free reconstruction of retained native-production evidence."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from time import time_ns

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from aiquanttrader.acceptance.audit import read_operational_events
from aiquanttrader.acceptance.models import (
    AcceptanceComponent,
    OperationalEventKind,
    OperationalEvidenceEvent,
)
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.execution import RiskReason
from aiquanttrader.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader.governance.models import (
    DeploymentAdmissionRecord,
    DeploymentAdmissionState,
    DeploymentArtifactKind,
    DeploymentArtifactManifest,
    DeploymentAuthorizationRenewal,
    DetachedApprovalSignature,
    VerifiedDeploymentRenewal,
)
from aiquanttrader.retirement.models import (
    NativeDrillEvidence,
    NativeProductionObservation,
    ProductionEvidenceArtifact,
    ProductionEvidenceCategory,
    ProductionEvidenceManifest,
    ProductionIncidentRegister,
    ProductionIncidentSeverity,
    RequiredNativeDrill,
    RetirementPolicy,
)

MAX_BUNDLE_FILES = 4_096
MAX_BUNDLE_BYTES = 1_073_741_824
MAX_CONTROL_BYTES = 1_048_576
MAX_PUBLIC_KEY_BYTES = 16_384
MAX_LEDGER_BYTES = 268_435_456

DRILL_CHECK_IDS: dict[RequiredNativeDrill, frozenset[str]] = {
    RequiredNativeDrill.NATIVE_ROLLBACK: frozenset(
        {
            "orders_canceled",
            "authorization_denied",
            "state_reconciled",
            "no_duplicate_orders",
        }
    ),
    RequiredNativeDrill.BACKUP_RESTORE: frozenset(
        {"backup_verified", "isolated_restore_completed", "restored_hashes_match"}
    ),
    RequiredNativeDrill.ALERT_DELIVERY: frozenset(
        {"critical_route_delivered", "operator_acknowledged_within_slo"}
    ),
    RequiredNativeDrill.OPERATOR_ACCESS: frozenset(
        {"break_glass_authenticated", "least_privilege_verified", "audit_event_retained"}
    ),
}

BREACH_RISK_REASONS = frozenset(
    {
        RiskReason.CAPITAL_LIMIT,
        RiskReason.DAILY_LOSS_LIMIT,
        RiskReason.DRAWDOWN_LIMIT,
        RiskReason.INVALID_ACCOUNT_STATE,
        RiskReason.LEVERAGE_LIMIT,
    }
)

APPROVAL_MANIFEST_FIELDS = (
    "deployment_id",
    "commit_sha",
    "image_digest",
    "configuration_sha256",
    "dependency_lock_sha256",
    "dataset_sha256",
    "model_sha256",
    "feature_schema_sha256",
    "strategy_config_sha256",
    "risk_policy_sha256",
    "shadow_evidence_sha256",
    "testnet_evidence_sha256",
    "canary_evidence_sha256",
    "rollback_deployment_id",
)


@dataclass(frozen=True, slots=True)
class VerifiedAuthorityChain:
    record: DeploymentAdmissionRecord
    approval: DeploymentApproval
    artifact_manifest: DeploymentArtifactManifest
    chain_sha256: str


def assemble_native_production_observation(
    root: Path,
    *,
    policy: RetirementPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> NativeProductionObservation:
    """Reconstruct one observation without network, signer, wallet, or action capability."""

    return _assemble_native_production_observation(
        root,
        policy=policy,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
        assembled_ts_ns=time_ns(),
    )


def _assemble_native_production_observation(
    root: Path,
    *,
    policy: RetirementPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
    assembled_ts_ns: int,
) -> NativeProductionObservation:
    evidence_root = _validated_root(root)
    manifest = _load_control(
        evidence_root / "production-manifest.json",
        ProductionEvidenceManifest,
    )
    if manifest.created_ts_ns > assembled_ts_ns:
        raise ValueError("production evidence manifest is dated after assembly")
    if policy.frozen_at_ns > manifest.started_ts_ns:
        raise ValueError("retirement policy was not frozen before production observation")
    bindings = {item.relative_path: item for item in manifest.artifacts}
    initial_inventory = _validate_inventory(evidence_root, bindings)
    authority = _verify_authority_chain(
        evidence_root,
        manifest,
        bindings,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
        assembled_ts_ns=assembled_ts_ns,
    )
    incident_register = _load_incident_register(evidence_root, manifest, bindings)
    drills = _load_drills(evidence_root, manifest, bindings)

    execution_events = _operational_events(
        evidence_root,
        bindings,
        category=ProductionEvidenceCategory.EXECUTION_AUDIT,
        component=AcceptanceComponent.EXECUTION,
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
    )
    sentinel_events = _operational_events(
        evidence_root,
        bindings,
        category=ProductionEvidenceCategory.SENTINEL_AUDIT,
        component=AcceptanceComponent.SENTINEL,
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
    )
    if not any(
        event.kind is OperationalEventKind.RECONCILIATION and event.success
        for event in execution_events
    ):
        raise ValueError("production execution audit has no successful reconciliation")
    health_sample_times = sorted(
        {
            event.event_ts_ns
            for event in sentinel_events
            if event.kind is OperationalEventKind.DEADMAN_SCHEDULE and event.success
        }
    )
    if not health_sample_times:
        raise ValueError("production sentinel audit has no successful dead-man schedule")
    continuity_points = [
        manifest.started_ts_ns,
        *health_sample_times,
        manifest.ended_ts_ns,
    ]
    maximum_operational_gap_ns = max(
        later - earlier for earlier, later in pairwise(continuity_points)
    )
    if maximum_operational_gap_ns > policy.maximum_native_operational_gap_ns:
        raise ValueError("production sentinel health evidence contains an excessive gap")

    runtime_critical_incidents = sum(
        not event.success and event.kind is OperationalEventKind.LIVE_PIPELINE_FAULT
        for event in execution_events
    ) + sum(
        not event.success
        and event.kind
        in {
            OperationalEventKind.DEADMAN_SCHEDULE,
            OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
        }
        for event in sentinel_events
    )
    registered_critical_incidents = sum(
        incident.severity is ProductionIncidentSeverity.CRITICAL
        for incident in incident_register.incidents
    )
    reconciliation_failures = sum(
        event.kind is OperationalEventKind.RECONCILIATION and not event.success
        for event in execution_events
    )
    risk_breaches = sum(
        event.kind is OperationalEventKind.RISK_STATE
        and bool(set(event.risk_reasons) & BREACH_RISK_REASONS)
        for event in execution_events
    )

    confirmed_inventory = _validate_inventory(evidence_root, bindings)
    if confirmed_inventory != initial_inventory:
        raise ValueError("production evidence changed during assembly")
    bundle_sha256 = canonical_sha256(
        {
            "schema_version": 1,
            "files": [
                {
                    "relative_path": relative_path,
                    "content_sha256": digest,
                    "byte_count": byte_count,
                }
                for relative_path, digest, byte_count in confirmed_inventory
            ],
        }
    )
    return NativeProductionObservation(
        retirement_id=manifest.retirement_id,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        deployment_id=manifest.deployment_id,
        admission_id=manifest.admission_id,
        terminal_authorization_id=authority.record.authorization_id,
        renewal_count=authority.record.renewal_count,
        authorization_expires_ts_ns=_datetime_ns(authority.record.expires_at),
        authorization_chain_sha256=authority.chain_sha256,
        approval_key_id=expected_key_id,
        approval_public_key_sha256=expected_public_key_sha256,
        production_approval_sha256=authority.approval.sha256(),
        production_artifact_manifest_sha256=authority.artifact_manifest.sha256(),
        evidence_manifest_sha256=manifest.sha256(),
        started_ts_ns=manifest.started_ts_ns,
        ended_ts_ns=manifest.ended_ts_ns,
        assembled_ts_ns=assembled_ts_ns,
        sentinel_health_samples=len(health_sample_times),
        maximum_operational_gap_ns=maximum_operational_gap_ns,
        critical_incidents=runtime_critical_incidents + registered_critical_incidents,
        reconciliation_failures=reconciliation_failures,
        risk_breaches=risk_breaches,
        completed_drills=tuple(drill for drill in RequiredNativeDrill if drills[drill].passed),
        evidence_bundle_sha256=bundle_sha256,
    )


def verify_native_production_observation(
    root: Path,
    observation: NativeProductionObservation,
    *,
    policy: RetirementPolicy,
    expected_key_id: str,
    expected_public_key_sha256: str,
) -> NativeProductionObservation:
    verified_ts_ns = time_ns()
    if (
        observation.approval_key_id != expected_key_id
        or observation.approval_public_key_sha256 != expected_public_key_sha256
    ):
        raise ValueError("native production observation uses a different approval trust root")
    if observation.assembled_ts_ns > verified_ts_ns:
        raise ValueError("native production observation is dated after verification")
    if observation.authorization_expires_ts_ns <= verified_ts_ns:
        raise ValueError("native production authorization is not active at verification")
    assembled = _assemble_native_production_observation(
        root,
        policy=policy,
        expected_key_id=expected_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
        assembled_ts_ns=observation.assembled_ts_ns,
    )
    if assembled != observation:
        raise ValueError("native production observation does not match its evidence bundle")
    return assembled


def load_native_production_observation(path: Path) -> NativeProductionObservation:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    observation = NativeProductionObservation.model_validate_json(payload)
    if payload != observation.canonical_bytes() + b"\n":
        raise ValueError("native production observation is not canonical JSON")
    return observation


def _verify_authority_chain(
    root: Path,
    manifest: ProductionEvidenceManifest,
    bindings: dict[str, ProductionEvidenceArtifact],
    *,
    expected_key_id: str,
    expected_public_key_sha256: str,
    assembled_ts_ns: int,
) -> VerifiedAuthorityChain:
    ledger_path = _one_path(root, bindings, ProductionEvidenceCategory.ADMISSION_LEDGER)
    record, history = _read_retained_ledger(ledger_path, manifest.deployment_id)
    if record is None:
        raise ValueError("production admission is absent from the retained ledger")
    if record.state is not DeploymentAdmissionState.ACTIVE:
        raise ValueError("retained production admission is not active")
    if record.stage is not PromotionStage.PRODUCTION:
        raise ValueError("retained admission is not a production deployment")
    if (
        record.deployment_id != manifest.deployment_id
        or record.admission_id != manifest.admission_id
    ):
        raise ValueError("production manifest and admission ledger identities differ")

    approval = _load_signed_model(
        _one_path(root, bindings, ProductionEvidenceCategory.DEPLOYMENT_APPROVAL),
        DeploymentApproval,
    )
    artifact_manifest = _load_signed_model(
        _one_path(root, bindings, ProductionEvidenceCategory.ARTIFACT_MANIFEST),
        DeploymentArtifactManifest,
    )
    signature = _load_signed_model(
        _one_path(root, bindings, ProductionEvidenceCategory.APPROVAL_SIGNATURE),
        DetachedApprovalSignature,
    )
    public_key = _load_public_key(
        _read_regular(
            _one_path(root, bindings, ProductionEvidenceCategory.APPROVAL_PUBLIC_KEY),
            maximum_bytes=MAX_PUBLIC_KEY_BYTES,
        )
    )
    public_key_sha256 = hashlib.sha256(
        public_key.export_key(format="DER", compress=False)
    ).hexdigest()
    if signature.key_id != expected_key_id:
        raise ValueError("production approval key identity differs from the pinned trust root")
    if public_key_sha256 != expected_public_key_sha256:
        raise ValueError("production approval public key differs from the pinned trust root")
    _verify_detached_signature(
        payload=approval.canonical_bytes(),
        payload_sha256=approval.sha256(),
        signature=signature,
        public_key=public_key,
        context="production approval",
    )
    if (
        approval.stage is not PromotionStage.PRODUCTION
        or artifact_manifest.stage is not approval.stage
    ):
        raise ValueError("retained approval and artifact manifest must be production stage")
    if artifact_manifest.created_at > approval.approved_at:
        raise ValueError("production approval predates its artifact manifest")
    if approval.artifact_manifest_sha256 != artifact_manifest.sha256():
        raise ValueError("production approval binds a different artifact manifest")
    for field in APPROVAL_MANIFEST_FIELDS:
        if getattr(approval, field) != getattr(artifact_manifest, field):
            raise ValueError(f"production approval and artifact manifest differ: {field}")
    _verify_release_artifacts(root, artifact_manifest, bindings)

    admission_payload = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "artifact_manifest": artifact_manifest.model_dump(mode="json"),
        "public_key_sha256": public_key_sha256,
        "signature_envelope_sha256": signature.sha256(),
    }
    admission_id = canonical_sha256(admission_payload)
    if admission_id != record.admission_id:
        raise ValueError("retained approval does not reproduce the admitted identity")
    if record.approval_public_key_sha256 != public_key_sha256:
        raise ValueError("retained approval trust root differs from the admission ledger")
    if not approval.is_active(record.admitted_at):
        raise ValueError("production approval was not active when admitted")
    _verify_record_identity(record, approval, artifact_manifest)
    if _datetime_ns(record.admitted_at) > manifest.started_ts_ns:
        raise ValueError("production observation starts before deployment admission")

    renewal_bindings = _by_reference(
        bindings,
        ProductionEvidenceCategory.AUTHORIZATION_RENEWAL,
    )
    renewal_signature_bindings = _by_reference(
        bindings,
        ProductionEvidenceCategory.AUTHORIZATION_RENEWAL_SIGNATURE,
    )
    if len(history) != len(renewal_bindings):
        raise ValueError("retained ledger and signed renewal inventories differ")
    history_by_id = {item.renewal.renewal_id: item for item in history}
    if len(history_by_id) != len(history) or set(history_by_id) != set(renewal_bindings):
        raise ValueError("retained ledger renewal identities are duplicated or incomplete")

    verified_by_prior: dict[str, VerifiedDeploymentRenewal] = {}
    for renewal_id, renewal_binding in renewal_bindings.items():
        authority = _load_signed_model(
            root / renewal_binding.relative_path,
            DeploymentAuthorizationRenewal,
        )
        if authority.renewal_id != renewal_id:
            raise ValueError("renewal artifact reference does not match its payload")
        renewal_signature = _load_signed_model(
            root / renewal_signature_bindings[renewal_id].relative_path,
            DetachedApprovalSignature,
        )
        if renewal_signature.key_id != signature.key_id:
            raise ValueError("renewal signing key identity differs from the admitted key")
        _verify_detached_signature(
            payload=authority.canonical_bytes(),
            payload_sha256=authority.sha256(),
            signature=renewal_signature,
            public_key=public_key,
            context=f"production renewal {renewal_id}",
        )
        stored = history_by_id[renewal_id]
        authorization_payload = {
            "schema_version": 1,
            "renewal": authority.model_dump(mode="json"),
            "public_key_sha256": public_key_sha256,
            "signature_envelope_sha256": renewal_signature.sha256(),
        }
        authorization_id = canonical_sha256(authorization_payload)
        if (
            authorization_id != stored.authorization_id
            or stored.renewal != authority
            or stored.public_key_sha256 != public_key_sha256
            or stored.signature_envelope_sha256 != renewal_signature.sha256()
        ):
            raise ValueError("signed renewal does not match the retained ledger history")
        if authority.prior_authorization_id in verified_by_prior:
            raise ValueError("production authorization chain forks at one predecessor")
        verified_by_prior[authority.prior_authorization_id] = stored

    chain_entries: list[dict[str, object]] = [
        {
            "authorization_id": admission_id,
            "prior_authorization_id": None,
            "approved_at": approval.approved_at.isoformat(),
            "admitted_at": record.admitted_at.isoformat(),
            "expires_at": approval.expires_at.isoformat(),
            "signature_envelope_sha256": signature.sha256(),
        }
    ]
    current_id = admission_id
    current_expiry = approval.expires_at
    while current_id in verified_by_prior:
        stored = verified_by_prior.pop(current_id)
        authority = stored.renewal
        if authority.approved_at > current_expiry or stored.verified_at >= current_expiry:
            raise ValueError("production authorization renewal contains an expiry gap")
        if authority.approved_at < record.admitted_at:
            raise ValueError("production authorization renewal predates admission")
        if authority.expires_at <= current_expiry:
            raise ValueError("production authorization renewal does not extend authority")
        _verify_renewal_identity(record, authority)
        current_id = stored.authorization_id
        current_expiry = authority.expires_at
        chain_entries.append(
            {
                "authorization_id": current_id,
                "prior_authorization_id": authority.prior_authorization_id,
                "approved_at": authority.approved_at.isoformat(),
                "verified_at": stored.verified_at.isoformat(),
                "expires_at": authority.expires_at.isoformat(),
                "signature_envelope_sha256": stored.signature_envelope_sha256,
            }
        )
    if verified_by_prior:
        raise ValueError("production authorization history is disconnected from admission")
    if (
        record.renewal_count != len(history)
        or record.authorization_id != current_id
        or record.expires_at != current_expiry
    ):
        raise ValueError("terminal production authorization disagrees with its signed chain")
    if _datetime_ns(current_expiry) <= assembled_ts_ns:
        raise ValueError("production authorization expired before evidence assembly")
    chain_payload = {
        "schema_version": 1,
        "deployment_id": record.deployment_id,
        "admission_id": record.admission_id,
        "authorizations": chain_entries,
    }
    return VerifiedAuthorityChain(
        record=record,
        approval=approval,
        artifact_manifest=artifact_manifest,
        chain_sha256=canonical_sha256(chain_payload),
    )


def _verify_record_identity(
    record: DeploymentAdmissionRecord,
    approval: DeploymentApproval,
    artifact_manifest: DeploymentArtifactManifest,
) -> None:
    expected: tuple[tuple[str, object, object], ...] = (
        ("deployment_id", record.deployment_id, approval.deployment_id),
        ("approval_id", record.approval_id, approval.approval_id),
        ("account_address", record.account_address.lower(), approval.account_address.lower()),
        ("vault_address", _lower(record.vault_address), _lower(approval.vault_address)),
        ("artifact_manifest_sha256", record.artifact_manifest_sha256, artifact_manifest.sha256()),
        ("configuration_sha256", record.configuration_sha256, approval.configuration_sha256),
        ("image_digest", record.image_digest, approval.image_digest),
        ("capital_limit_usd", record.capital_limit_usd, approval.capital_limit_usd),
    )
    for field, actual, wanted in expected:
        if actual != wanted:
            raise ValueError(f"production admission record mismatch: {field}")


def _verify_renewal_identity(
    record: DeploymentAdmissionRecord,
    authority: DeploymentAuthorizationRenewal,
) -> None:
    expected: tuple[tuple[str, object, object], ...] = (
        ("deployment_id", authority.deployment_id, record.deployment_id),
        ("initial_approval_id", authority.initial_approval_id, record.approval_id),
        ("admission_id", authority.admission_id, record.admission_id),
        ("account_address", authority.account_address.lower(), record.account_address.lower()),
        ("vault_address", _lower(authority.vault_address), _lower(record.vault_address)),
        (
            "artifact_manifest_sha256",
            authority.artifact_manifest_sha256,
            record.artifact_manifest_sha256,
        ),
        (
            "configuration_sha256",
            authority.configuration_sha256,
            record.configuration_sha256,
        ),
        ("image_digest", authority.image_digest, record.image_digest),
        ("capital_limit_usd", authority.capital_limit_usd, record.capital_limit_usd),
    )
    for field, actual, wanted in expected:
        if actual != wanted:
            raise ValueError(f"production renewal changed immutable identity: {field}")


def _verify_release_artifacts(
    root: Path,
    artifact_manifest: DeploymentArtifactManifest,
    bindings: dict[str, ProductionEvidenceArtifact],
) -> None:
    release = _by_reference(bindings, ProductionEvidenceCategory.RELEASE_ARTIFACT)
    by_kind = {binding.kind: binding for binding in artifact_manifest.artifacts}
    if set(by_kind) != set(DeploymentArtifactKind) or set(release) != {
        kind.value for kind in DeploymentArtifactKind
    }:
        raise ValueError("production release artifact set is incomplete")
    for kind, approved in by_kind.items():
        retained = release[kind.value]
        expected_path = f"raw/release/artifacts/{approved.relative_path}"
        if retained.relative_path != expected_path:
            raise ValueError(f"production release artifact path differs: {kind.value}")
        if retained.content_sha256 != approved.content_sha256:
            raise ValueError(f"production release artifact hash differs: {kind.value}")
        if _sha256_regular(root / retained.relative_path) != approved.content_sha256:
            raise ValueError(f"production release artifact content differs: {kind.value}")


def _load_incident_register(
    root: Path,
    manifest: ProductionEvidenceManifest,
    bindings: dict[str, ProductionEvidenceArtifact],
) -> ProductionIncidentRegister:
    register = _load_control(
        _one_path(root, bindings, ProductionEvidenceCategory.INCIDENT_REGISTER),
        ProductionIncidentRegister,
    )
    if (
        register.deployment_id != manifest.deployment_id
        or register.admission_id != manifest.admission_id
        or register.started_ts_ns != manifest.started_ts_ns
        or register.ended_ts_ns != manifest.ended_ts_ns
        or register.reviewed_ts_ns > manifest.created_ts_ns
    ):
        raise ValueError("production incident register lineage or interval differs")
    for incident in register.incidents:
        _validate_references(incident.evidence_paths, bindings, context="production incident")
    return register


def _load_drills(
    root: Path,
    manifest: ProductionEvidenceManifest,
    bindings: dict[str, ProductionEvidenceArtifact],
) -> dict[RequiredNativeDrill, NativeDrillEvidence]:
    drill_bindings = _by_reference(bindings, ProductionEvidenceCategory.DRILL_REPORT)
    drills: dict[RequiredNativeDrill, NativeDrillEvidence] = {}
    for drill in RequiredNativeDrill:
        report = _load_control(
            root / drill_bindings[drill.value].relative_path, NativeDrillEvidence
        )
        if report.drill is not drill:
            raise ValueError("native drill artifact reference does not match its payload")
        if (
            report.started_ts_ns < manifest.started_ts_ns
            or report.ended_ts_ns > manifest.ended_ts_ns
        ):
            raise ValueError(f"native drill escapes the production interval: {drill.value}")
        if {check.check_id for check in report.checks} != DRILL_CHECK_IDS[drill]:
            raise ValueError(f"native drill check set is incomplete: {drill.value}")
        if not report.passed:
            raise ValueError(f"native drill did not pass: {drill.value}")
        _validate_references(report.evidence_paths, bindings, context=f"{drill.value} drill")
        drills[drill] = report
    return drills


def _validate_references(
    paths: tuple[str, ...],
    bindings: dict[str, ProductionEvidenceArtifact],
    *,
    context: str,
) -> None:
    for path in paths:
        binding = bindings.get(path)
        if binding is None:
            raise ValueError(f"{context} references unbound evidence: {path}")
        if binding.category in {
            ProductionEvidenceCategory.DRILL_REPORT,
            ProductionEvidenceCategory.INCIDENT_REGISTER,
        }:
            raise ValueError(f"{context} cannot use a control report as supporting proof")


def _operational_events(
    root: Path,
    bindings: dict[str, ProductionEvidenceArtifact],
    *,
    category: ProductionEvidenceCategory,
    component: AcceptanceComponent,
    started_ts_ns: int,
    ended_ts_ns: int,
) -> tuple[OperationalEvidenceEvent, ...]:
    events = read_operational_events(
        _one_path(root, bindings, category),
        expected_component=component,
    )
    if any(later.event_ts_ns < earlier.event_ts_ns for earlier, later in pairwise(events)):
        raise ValueError(f"{component.value} operational event time moves backward")
    return tuple(event for event in events if started_ts_ns <= event.event_ts_ns <= ended_ts_ns)


def _read_retained_ledger(
    path: Path,
    deployment_id: str,
) -> tuple[DeploymentAdmissionRecord | None, tuple[VerifiedDeploymentRenewal, ...]]:
    if path.stat().st_size > MAX_LEDGER_BYTES:
        raise ValueError("retained admission ledger exceeds its hard size bound")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        result = connection.execute("PRAGMA quick_check").fetchone()
        schema = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        row = connection.execute(
            "SELECT record_json FROM admissions WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        renewals = connection.execute(
            """
            SELECT renewal_json FROM renewals
            WHERE deployment_id = ? ORDER BY approved_at, authorization_id
            """,
            (deployment_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("cannot verify retained admission ledger") from exc
    finally:
        if "connection" in locals():
            connection.close()
    if result is None or result[0] != "ok":
        raise ValueError("retained admission ledger failed SQLite integrity check")
    if schema is None or schema[0] != "2":
        raise ValueError("retained admission ledger schema is unsupported")
    record = None if row is None else DeploymentAdmissionRecord.model_validate_json(row[0])
    history = tuple(VerifiedDeploymentRenewal.model_validate_json(item[0]) for item in renewals)
    return record, history


def _validated_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("production evidence root must be absolute")
    if root.is_symlink():
        raise ValueError("production evidence root must be a non-symlink directory")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("production evidence root must be a non-symlink directory")
    return resolved


def _validate_inventory(
    root: Path,
    bindings: dict[str, ProductionEvidenceArtifact],
) -> tuple[tuple[str, str, int], ...]:
    expected = {"production-manifest.json", *bindings}
    actual: dict[str, Path] = {}
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"production evidence cannot contain symlinks: {path.name}")
        if path.is_dir():
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"production evidence contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"production evidence file is group/world writable: {path.name}")
        relative = path.relative_to(root).as_posix()
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("production evidence bundle exceeds its hard resource bounds")
    if set(actual) != expected:
        missing = sorted(expected - set(actual))
        extra = sorted(set(actual) - expected)
        raise ValueError(
            f"production evidence inventory mismatch: missing={missing}, extra={extra}"
        )
    inventory: list[tuple[str, str, int]] = []
    for relative, path in sorted(actual.items()):
        digest = _sha256_regular(path)
        size = path.stat().st_size
        binding = bindings.get(relative)
        if binding is not None and (binding.content_sha256 != digest or binding.byte_count != size):
            raise ValueError(f"production evidence digest or size mismatch: {relative}")
        inventory.append((relative, digest, size))
    return tuple(inventory)


def _one_path(
    root: Path,
    bindings: dict[str, ProductionEvidenceArtifact],
    category: ProductionEvidenceCategory,
) -> Path:
    matches = [item for item in bindings.values() if item.category is category]
    if len(matches) != 1:
        raise ValueError(f"production evidence requires exactly one {category.value}")
    return root / matches[0].relative_path


def _by_reference(
    bindings: dict[str, ProductionEvidenceArtifact],
    category: ProductionEvidenceCategory,
) -> dict[str, ProductionEvidenceArtifact]:
    return {item.reference_id: item for item in bindings.values() if item.category is category}


def _load_control[ModelT: DomainModel](path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"production control file is not canonical JSON: {path.name}")
    return value


def _load_signed_model[ModelT: DomainModel](path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes():
        raise ValueError(f"signed production artifact is not canonical JSON: {path.name}")
    return value


def _load_public_key(payload: bytes) -> ECC.EccKey:
    try:
        key = ECC.import_key(payload)
    except (ValueError, IndexError, TypeError) as exc:
        raise ValueError("production approval public key cannot be parsed") from exc
    if key.has_private() or key.curve != "Ed25519":
        raise ValueError("production evidence must contain only an Ed25519 public key")
    return key


def _verify_detached_signature(
    *,
    payload: bytes,
    payload_sha256: str,
    signature: DetachedApprovalSignature,
    public_key: ECC.EccKey,
    context: str,
) -> None:
    if signature.approval_sha256 != payload_sha256:
        raise ValueError(f"{context} signature binds different canonical bytes")
    try:
        eddsa.new(public_key, "rfc8032").verify(payload, signature.signature_bytes())
    except ValueError as exc:
        raise ValueError(f"{context} Ed25519 signature is invalid") from exc


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open production evidence file: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"production evidence is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ValueError(f"production evidence size is invalid: {path.name}")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(payload) != metadata.st_size or any(
            getattr(metadata, field) != getattr(final, field) for field in identity
        ):
            raise ValueError(f"production evidence changed while read: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _sha256_regular(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot hash production evidence file: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size <= 0:
            raise ValueError(f"production evidence is not a non-empty regular file: {path.name}")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
        final = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if observed != initial.st_size or any(
            getattr(initial, field) != getattr(final, field) for field in identity
        ):
            raise ValueError(f"production evidence changed while hashed: {path.name}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _datetime_ns(value: datetime) -> int:
    instant = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = instant - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _lower(value: str | None) -> str | None:
    return None if value is None else value.lower()
