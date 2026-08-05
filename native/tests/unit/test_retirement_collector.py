from __future__ import annotations

import base64
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import ValidationError

import aiquanttrader_native.retirement.collector as collector_module
from aiquanttrader_native.acceptance.audit import OperationalEvidenceLog
from aiquanttrader_native.acceptance.models import (
    AcceptanceComponent,
    OperationalEventKind,
    OperationalEvidenceEvent,
)
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.domain.execution import RiskReason, RiskState
from aiquanttrader_native.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader_native.governance.ledger import DeploymentAdmissionLedger
from aiquanttrader_native.governance.models import (
    DeploymentArtifactBinding,
    DeploymentArtifactKind,
    DeploymentArtifactManifest,
    DeploymentAuthorizationRenewal,
    DetachedApprovalSignature,
    VerifiedDeploymentAdmission,
    VerifiedDeploymentRenewal,
)
from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.collector import (
    DRILL_CHECK_IDS,
    _load_public_key,
    _read_regular,
    _sha256_regular,
    _validated_root,
    _verify_detached_signature,
    assemble_native_production_observation,
    load_native_production_observation,
    verify_native_production_observation,
)
from aiquanttrader_native.retirement.models import (
    LegacyArchiveArtifactKind,
    LegacyCapability,
    NativeDrillCheck,
    NativeDrillEvidence,
    ProductionEvidenceArtifact,
    ProductionEvidenceCategory,
    ProductionEvidenceManifest,
    ProductionIncident,
    ProductionIncidentRegister,
    ProductionIncidentSeverity,
    RequiredNativeDrill,
    RetirementPolicy,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)
OBSERVATION_START = BASE + timedelta(hours=1)
OBSERVATION_END = BASE + timedelta(days=20, minutes=30)
CREATED = BASE + timedelta(days=20, hours=2)
COMMIT = "1" * 40
IMAGE = "sha256:" + "2" * 64
ACCOUNT = "0x" + "3" * 40
TRADING = "0x" + "4" * 40
CONTROL = "0x" + "5" * 40
APPROVAL_KEY_ID = "production-approver-001"


@pytest.fixture(autouse=True)
def _fixed_assembly_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_module, "time_ns", lambda: _timestamp_ns(CREATED))


def _policy() -> RetirementPolicy:
    return RetirementPolicy(
        policy_id="retirement-collector-test",
        frozen_at_ns=_timestamp_ns(BASE - timedelta(days=1)),
        minimum_native_production_observation_ns=1,
        maximum_native_operational_gap_ns=int(timedelta(days=11).total_seconds() * 1_000_000_000),
        minimum_disabled_observation_ns=1,
        minimum_archive_retention_ns=1,
        maximum_final_state_capture_skew_ns=1,
        maximum_final_state_age_ns=1,
        archive_credential_scan_policy_id="credential-scan-test",
        archive_credential_scan_policy_sha256="6" * 64,
        required_archive_artifacts=tuple(LegacyArchiveArtifactKind),
        required_disabled_capabilities=tuple(LegacyCapability),
        required_native_drills=tuple(RequiredNativeDrill),
    )


def _policy_path(tmp_path: Path) -> Path:
    policy = _policy()
    path = (tmp_path / "retirement-policy.toml").resolve()
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'policy_id = "{policy.policy_id}"',
                f"frozen_at_ns = {policy.frozen_at_ns}",
                "minimum_native_production_observation_ns = 1",
                f"maximum_native_operational_gap_ns = {policy.maximum_native_operational_gap_ns}",
                "minimum_disabled_observation_ns = 1",
                "minimum_archive_retention_ns = 1",
                "maximum_final_state_capture_skew_ns = 1",
                "maximum_final_state_age_ns = 1",
                'archive_credential_scan_policy_id = "credential-scan-test"',
                'archive_credential_scan_policy_sha256 = "' + "6" * 64 + '"',
                "required_archive_artifacts = ["
                + ",".join(f'"{item.value}"' for item in LegacyArchiveArtifactKind)
                + "]",
                "required_disabled_capabilities = ["
                + ",".join(f'"{item.value}"' for item in LegacyCapability)
                + "]",
                "required_native_drills = ["
                + ",".join(f'"{item.value}"' for item in RequiredNativeDrill)
                + "]",
            )
        ),
        encoding="utf-8",
    )
    return path


def _timestamp_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _deployment(
    *,
    stage: PromotionStage,
    deployment_id: str,
    approval_id: str,
    rollback_deployment_id: str,
    prior_approval_id: str | None,
    approved_at: datetime,
    expires_at: datetime,
    key: ECC.EccKey,
) -> tuple[
    VerifiedDeploymentAdmission,
    DeploymentApproval,
    DeploymentArtifactManifest,
    DetachedApprovalSignature,
    dict[DeploymentArtifactKind, bytes],
]:
    kinds = list(DeploymentArtifactKind)
    if stage is PromotionStage.APPROVED_CANARY:
        kinds.remove(DeploymentArtifactKind.CANARY_EVIDENCE)
    payloads = {kind: f"retained-{kind.value}\n".encode() for kind in kinds}
    digests = {kind: hashlib.sha256(payload).hexdigest() for kind, payload in payloads.items()}
    artifact_manifest = DeploymentArtifactManifest(
        deployment_id=deployment_id,
        stage=stage,  # type: ignore[arg-type]
        created_at=approved_at - timedelta(hours=1),
        commit_sha=COMMIT,
        image_digest=IMAGE,
        configuration_sha256="6" * 64,
        dependency_lock_sha256=digests[DeploymentArtifactKind.DEPENDENCY_LOCK],
        dataset_sha256=digests[DeploymentArtifactKind.DATASET_MANIFEST],
        model_sha256=digests[DeploymentArtifactKind.MODEL_MANIFEST],
        feature_schema_sha256=digests[DeploymentArtifactKind.FEATURE_SCHEMA],
        strategy_config_sha256=digests[DeploymentArtifactKind.STRATEGY_CONFIG],
        risk_policy_sha256=digests[DeploymentArtifactKind.RISK_POLICY],
        shadow_evidence_sha256=digests[DeploymentArtifactKind.SHADOW_EVIDENCE],
        testnet_evidence_sha256=digests[DeploymentArtifactKind.TESTNET_EVIDENCE],
        canary_evidence_sha256=digests.get(DeploymentArtifactKind.CANARY_EVIDENCE),
        rollback_deployment_id=rollback_deployment_id,
        artifacts=tuple(
            DeploymentArtifactBinding(
                kind=kind,
                relative_path=f"{kind.value}.bin",
                content_sha256=digests[kind],
            )
            for kind in kinds
        ),
    )
    approval = DeploymentApproval(
        approval_id=approval_id,
        deployment_id=deployment_id,
        stage=stage,  # type: ignore[arg-type]
        account_address=ACCOUNT,
        trading_wallet_address=TRADING,
        control_wallet_address=CONTROL,
        commit_sha=COMMIT,
        image_digest=IMAGE,
        artifact_manifest_sha256=artifact_manifest.sha256(),
        dependency_lock_sha256=artifact_manifest.dependency_lock_sha256,
        dataset_sha256=artifact_manifest.dataset_sha256,
        model_sha256=artifact_manifest.model_sha256,
        configuration_sha256=artifact_manifest.configuration_sha256,
        feature_schema_sha256=artifact_manifest.feature_schema_sha256,
        strategy_config_sha256=artifact_manifest.strategy_config_sha256,
        risk_policy_sha256=artifact_manifest.risk_policy_sha256,
        shadow_evidence_sha256=artifact_manifest.shadow_evidence_sha256,
        testnet_evidence_sha256=artifact_manifest.testnet_evidence_sha256,
        canary_evidence_sha256=artifact_manifest.canary_evidence_sha256,
        capital_limit_usd=Decimal("1000"),
        rollback_deployment_id=rollback_deployment_id,
        prior_approval_id=prior_approval_id,
        approver="risk-owner",
        approved_at=approved_at,
        expires_at=expires_at,
    )
    envelope = DetachedApprovalSignature(
        key_id=APPROVAL_KEY_ID,
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(
            eddsa.new(key, "rfc8032").sign(approval.canonical_bytes())
        ).decode("ascii"),
    )
    fingerprint = hashlib.sha256(
        key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()
    identity = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "artifact_manifest": artifact_manifest.model_dump(mode="json"),
        "public_key_sha256": fingerprint,
        "signature_envelope_sha256": envelope.sha256(),
    }
    verified = VerifiedDeploymentAdmission.model_validate(
        {
            **identity,
            "admission_id": canonical_sha256(identity),
            "verified_at": (approved_at + timedelta(minutes=1)).isoformat(),
        }
    )
    return verified, approval, artifact_manifest, envelope, payloads


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _trust(root: Path) -> tuple[str, str]:
    key = ECC.import_key((root / "raw/authorization/approver.pub").read_bytes())
    fingerprint = hashlib.sha256(key.export_key(format="DER", compress=False)).hexdigest()
    return APPROVAL_KEY_ID, fingerprint


def _binding(
    root: Path,
    *,
    category: ProductionEvidenceCategory,
    reference_id: str,
    relative_path: str,
    start: datetime = BASE,
    end: datetime = CREATED,
) -> ProductionEvidenceArtifact:
    payload = (root / relative_path).read_bytes()
    return ProductionEvidenceArtifact(
        category=category,
        reference_id=reference_id,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_start_ts_ns=_timestamp_ns(start),
        captured_end_ts_ns=_timestamp_ns(end),
    )


def _renew(
    *,
    ledger: DeploymentAdmissionLedger,
    root: Path,
    key: ECC.EccKey,
    index: int,
    approved_at: datetime,
    expires_at: datetime,
) -> tuple[str, str, str]:
    current = ledger.get("production-001")
    assert current is not None
    renewal_id = f"production-renewal-{index:03d}"
    authority = DeploymentAuthorizationRenewal(
        renewal_id=renewal_id,
        deployment_id=current.deployment_id,
        initial_approval_id=current.approval_id,
        admission_id=current.admission_id,
        prior_authorization_id=current.authorization_id,
        account_address=current.account_address,
        vault_address=current.vault_address,
        artifact_manifest_sha256=current.artifact_manifest_sha256,
        configuration_sha256=current.configuration_sha256,
        image_digest=current.image_digest,
        capital_limit_usd=current.capital_limit_usd,
        approver="risk-owner",
        approved_at=approved_at,
        expires_at=expires_at,
    )
    signature = DetachedApprovalSignature(
        key_id=APPROVAL_KEY_ID,
        approval_sha256=authority.sha256(),
        signature_base64=base64.b64encode(
            eddsa.new(key, "rfc8032").sign(authority.canonical_bytes())
        ).decode("ascii"),
    )
    fingerprint = hashlib.sha256(
        key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()
    identity = {
        "schema_version": 1,
        "renewal": authority.model_dump(mode="json"),
        "public_key_sha256": fingerprint,
        "signature_envelope_sha256": signature.sha256(),
    }
    verified_at = approved_at + timedelta(minutes=1)
    verified = VerifiedDeploymentRenewal.model_validate(
        {
            **identity,
            "authorization_id": canonical_sha256(identity),
            "verified_at": verified_at.isoformat(),
        }
    )
    ledger.renew(
        verified,
        actor="release-operator",
        reason=f"reviewed production renewal {index}",
        now=verified_at,
    )
    renewal_path = f"raw/authorization/renewals/{renewal_id}.json"
    signature_path = f"raw/authorization/renewals/{renewal_id}.sig.json"
    _write(root / renewal_path, authority.canonical_bytes())
    _write(root / signature_path, signature.canonical_bytes())
    return renewal_id, renewal_path, signature_path


def _bundle(tmp_path: Path) -> tuple[Path, ProductionEvidenceManifest]:
    root = (tmp_path / "production-evidence").resolve()
    root.mkdir(parents=True)
    key = ECC.generate(curve="Ed25519")
    canary, *_ = _deployment(
        stage=PromotionStage.APPROVED_CANARY,
        deployment_id="canary-001",
        approval_id="canary-approval-001",
        rollback_deployment_id="safe-000",
        prior_approval_id=None,
        approved_at=BASE - timedelta(hours=3),
        expires_at=BASE + timedelta(days=1),
        key=key,
    )
    production, approval, artifact_manifest, approval_signature, payloads = _deployment(
        stage=PromotionStage.PRODUCTION,
        deployment_id="production-001",
        approval_id="production-approval-001",
        rollback_deployment_id="canary-001",
        prior_approval_id="canary-approval-001",
        approved_at=BASE,
        expires_at=BASE + timedelta(days=7),
        key=key,
    )

    ledger_path = root / "raw/authorization/admissions.sqlite3"
    ledger = DeploymentAdmissionLedger(ledger_path)
    ledger.admit(
        canary,
        actor="operator",
        reason="reviewed canary admission",
        now=BASE - timedelta(hours=2),
    )
    ledger.admit(
        production,
        actor="operator",
        reason="reviewed production scale admission",
        now=BASE + timedelta(minutes=30),
    )
    renewal_paths = tuple(
        _renew(
            ledger=ledger,
            root=root,
            key=key,
            index=index,
            approved_at=BASE + timedelta(days=approved_day),
            expires_at=BASE + timedelta(days=expiry_day),
        )
        for index, (approved_day, expiry_day) in enumerate(
            ((5, 12), (10, 17), (15, 22), (20, 27)),
            start=1,
        )
    )
    ledger.close()

    approval_path = "raw/authorization/deployment-approval.json"
    approval_signature_path = "raw/authorization/deployment-approval.sig.json"
    public_key_path = "raw/authorization/approver.pub"
    artifact_manifest_path = "raw/release/artifact-manifest.json"
    _write(root / approval_path, approval.canonical_bytes())
    _write(root / approval_signature_path, approval_signature.canonical_bytes())
    _write(
        root / public_key_path,
        key.public_key().export_key(format="PEM").encode("ascii"),
    )
    _write(root / artifact_manifest_path, artifact_manifest.canonical_bytes())
    for binding in artifact_manifest.artifacts:
        _write(
            root / f"raw/release/artifacts/{binding.relative_path}",
            payloads[binding.kind],
        )

    execution_path = root / "raw/operations/execution-audit.jsonl"
    OperationalEvidenceLog(
        execution_path,
        component=AcceptanceComponent.EXECUTION,
    ).append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=_timestamp_ns(OBSERVATION_START + timedelta(minutes=1)),
        success=True,
        detail="production startup reconciliation completed",
    )
    OperationalEvidenceLog(
        execution_path,
        component=AcceptanceComponent.EXECUTION,
    ).append(
        kind=OperationalEventKind.RISK_STATE,
        event_ts_ns=_timestamp_ns(OBSERVATION_START + timedelta(minutes=2)),
        success=True,
        detail="active",
        risk_state=RiskState.ACTIVE,
    )
    sentinel_path = root / "raw/operations/sentinel-audit.jsonl"
    sentinel = OperationalEvidenceLog(
        sentinel_path,
        component=AcceptanceComponent.SENTINEL,
    )
    for sampled_at in (
        OBSERVATION_START + timedelta(minutes=1),
        BASE + timedelta(days=10),
        OBSERVATION_END - timedelta(minutes=1),
    ):
        sentinel.append(
            kind=OperationalEventKind.DEADMAN_SCHEDULE,
            event_ts_ns=_timestamp_ns(sampled_at),
            success=True,
            detail="dead-man cancellation renewed",
        )

    incident_path = "raw/operations/incident-register.json"
    incident_register = ProductionIncidentRegister(
        deployment_id=production.approval.deployment_id,
        admission_id=production.admission_id,
        started_ts_ns=_timestamp_ns(OBSERVATION_START),
        ended_ts_ns=_timestamp_ns(OBSERVATION_END),
        reviewed_ts_ns=_timestamp_ns(CREATED - timedelta(minutes=1)),
        reviewer="independent-risk-reviewer",
    )
    _write(root / incident_path, incident_register.canonical_bytes() + b"\n")

    drill_paths: list[tuple[RequiredNativeDrill, str, str]] = []
    for index, drill in enumerate(RequiredNativeDrill, start=1):
        supporting_path = f"raw/support/{drill.value}.txt"
        _write(root / supporting_path, f"retained proof for {drill.value}\n".encode())
        report_path = f"raw/drills/{drill.value}.json"
        report = NativeDrillEvidence(
            drill=drill,
            started_ts_ns=_timestamp_ns(BASE + timedelta(days=index)),
            ended_ts_ns=_timestamp_ns(BASE + timedelta(days=index, minutes=10)),
            checks=tuple(
                NativeDrillCheck(
                    check_id=check_id,
                    passed=True,
                    actual="retained evidence passed",
                    required="pass",
                )
                for check_id in sorted(DRILL_CHECK_IDS[drill])
            ),
            evidence_paths=(supporting_path,),
        )
        _write(root / report_path, report.canonical_bytes() + b"\n")
        drill_paths.append((drill, report_path, supporting_path))

    artifacts = [
        _binding(
            root,
            category=ProductionEvidenceCategory.ADMISSION_LEDGER,
            reference_id="production-admission-ledger",
            relative_path="raw/authorization/admissions.sqlite3",
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.DEPLOYMENT_APPROVAL,
            reference_id="production-approval-001",
            relative_path=approval_path,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.APPROVAL_SIGNATURE,
            reference_id="production-approval-001",
            relative_path=approval_signature_path,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.APPROVAL_PUBLIC_KEY,
            reference_id=APPROVAL_KEY_ID,
            relative_path=public_key_path,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.ARTIFACT_MANIFEST,
            reference_id="production-001",
            relative_path=artifact_manifest_path,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.EXECUTION_AUDIT,
            reference_id="production-execution-audit",
            relative_path="raw/operations/execution-audit.jsonl",
            start=OBSERVATION_START,
            end=OBSERVATION_END,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.SENTINEL_AUDIT,
            reference_id="production-sentinel-audit",
            relative_path="raw/operations/sentinel-audit.jsonl",
            start=OBSERVATION_START,
            end=OBSERVATION_END,
        ),
        _binding(
            root,
            category=ProductionEvidenceCategory.INCIDENT_REGISTER,
            reference_id="production-incident-register",
            relative_path=incident_path,
            start=OBSERVATION_START,
            end=OBSERVATION_END,
        ),
    ]
    artifacts.extend(
        _binding(
            root,
            category=ProductionEvidenceCategory.RELEASE_ARTIFACT,
            reference_id=binding.kind.value,
            relative_path=f"raw/release/artifacts/{binding.relative_path}",
        )
        for binding in artifact_manifest.artifacts
    )
    for renewal_id, renewal_path, signature_path in renewal_paths:
        artifacts.extend(
            (
                _binding(
                    root,
                    category=ProductionEvidenceCategory.AUTHORIZATION_RENEWAL,
                    reference_id=renewal_id,
                    relative_path=renewal_path,
                ),
                _binding(
                    root,
                    category=ProductionEvidenceCategory.AUTHORIZATION_RENEWAL_SIGNATURE,
                    reference_id=renewal_id,
                    relative_path=signature_path,
                ),
            )
        )
    for drill, report_path, supporting_path in drill_paths:
        artifacts.extend(
            (
                _binding(
                    root,
                    category=ProductionEvidenceCategory.DRILL_REPORT,
                    reference_id=drill.value,
                    relative_path=report_path,
                ),
                _binding(
                    root,
                    category=ProductionEvidenceCategory.SUPPORTING_EVIDENCE,
                    reference_id=f"{drill.value}-proof",
                    relative_path=supporting_path,
                ),
            )
        )
    manifest = ProductionEvidenceManifest(
        retirement_id="retirement-native-001",
        deployment_id=production.approval.deployment_id,
        admission_id=production.admission_id,
        started_ts_ns=_timestamp_ns(OBSERVATION_START),
        ended_ts_ns=_timestamp_ns(OBSERVATION_END),
        created_ts_ns=_timestamp_ns(CREATED),
        artifacts=tuple(artifacts),
    )
    _write(root / "production-manifest.json", manifest.canonical_bytes() + b"\n")
    return root, manifest


def _rebind(root: Path, relative_path: str) -> None:
    manifest_path = root / "production-manifest.json"
    manifest = ProductionEvidenceManifest.model_validate_json(manifest_path.read_bytes())
    payload = (root / relative_path).read_bytes()
    updated = tuple(
        item.model_copy(
            update={
                "content_sha256": hashlib.sha256(payload).hexdigest(),
                "byte_count": len(payload),
            }
        )
        if item.relative_path == relative_path
        else item
        for item in manifest.artifacts
    )
    manifest_path.write_bytes(
        manifest.model_copy(update={"artifacts": updated}).canonical_bytes() + b"\n"
    )


def test_native_production_assembler_verifies_full_signed_chain_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    observation = assemble_native_production_observation(
        root,
        policy=_policy(),
        expected_key_id=key_id,
        expected_public_key_sha256=fingerprint,
    )

    assert observation.deployment_id == manifest.deployment_id
    assert observation.admission_id == manifest.admission_id
    assert observation.renewal_count == 4
    assert observation.terminal_authorization_id != observation.admission_id
    assert observation.authorization_expires_ts_ns == _timestamp_ns(BASE + timedelta(days=27))
    assert observation.critical_incidents == 0
    assert observation.reconciliation_failures == 0
    assert observation.risk_breaches == 0
    assert set(observation.completed_drills) == set(RequiredNativeDrill)
    assert (
        verify_native_production_observation(
            root,
            observation,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
        == observation
    )
    with pytest.raises(ValueError, match="pinned trust root"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="excessive gap"):
        assemble_native_production_observation(
            root,
            policy=_policy().model_copy(
                update={"maximum_native_operational_gap_ns": 86_400_000_000_000}
            ),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
    with pytest.raises(ValueError, match="different approval trust root"):
        verify_native_production_observation(
            root,
            observation,
            policy=_policy(),
            expected_key_id="different-key",
            expected_public_key_sha256=fingerprint,
        )

    output = (tmp_path / "native-observation.json").resolve()
    policy_path = _policy_path(tmp_path)
    assert (
        retirement_main(
            [
                "assemble-native",
                "--evidence-root",
                str(root),
                "--policy",
                str(policy_path),
                "--output",
                str(output),
                "--approval-key-id",
                key_id,
                "--approval-public-key-sha256",
                fingerprint,
            ]
        )
        == 0
    )
    assert output.read_bytes() == observation.canonical_bytes() + b"\n"
    assert (
        retirement_main(
            [
                "verify-native",
                "--evidence-root",
                str(root),
                "--policy",
                str(policy_path),
                "--observation",
                str(output),
                "--approval-key-id",
                key_id,
                "--approval-public-key-sha256",
                fingerprint,
            ]
        )
        == 0
    )
    assert observation.terminal_authorization_id in capsys.readouterr().out


def test_native_production_assembler_rejects_signature_and_file_tampering(
    tmp_path: Path,
) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    signature_path = "raw/authorization/deployment-approval.sig.json"
    signature = DetachedApprovalSignature.model_validate_json((root / signature_path).read_bytes())
    (root / signature_path).write_bytes(
        signature.model_copy(
            update={"signature_base64": base64.b64encode(b"x" * 64).decode("ascii")}
        ).canonical_bytes()
    )
    _rebind(root, signature_path)
    with pytest.raises(ValueError, match="signature is invalid"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )

    root, _manifest = _bundle(tmp_path / "second")
    key_id, fingerprint = _trust(root)
    support = root / "raw/support/native_rollback.txt"
    support.write_text("tampered proof\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest or size mismatch"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )


def test_native_production_assembler_rejects_ledger_chain_mismatch(tmp_path: Path) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    ledger_path = root / "raw/authorization/admissions.sqlite3"
    connection = sqlite3.connect(ledger_path)
    try:
        row = connection.execute(
            "SELECT record_json FROM admissions WHERE deployment_id = 'production-001'"
        ).fetchone()
        assert row is not None
        from aiquanttrader_native.governance.models import DeploymentAdmissionRecord

        record = DeploymentAdmissionRecord.model_validate_json(row[0])
        changed = record.model_copy(update={"renewal_count": record.renewal_count - 1})
        connection.execute(
            "UPDATE admissions SET renewal_count = ?, record_json = ? WHERE deployment_id = ?",
            (changed.renewal_count, changed.model_dump_json(), changed.deployment_id),
        )
        connection.commit()
    finally:
        connection.close()
    _rebind(root, "raw/authorization/admissions.sqlite3")
    with pytest.raises(ValueError, match="terminal production authorization"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )


def test_native_production_assembler_derives_failures_from_typed_audits(
    tmp_path: Path,
) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    audit_path = root / "raw/operations/execution-audit.jsonl"
    audit = OperationalEvidenceLog(audit_path, component=AcceptanceComponent.EXECUTION)
    audit.append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=_timestamp_ns(BASE + timedelta(days=2)),
        success=False,
        detail="reconciliation failed closed",
    )
    audit.append(
        kind=OperationalEventKind.RISK_STATE,
        event_ts_ns=_timestamp_ns(BASE + timedelta(days=3)),
        success=True,
        detail=RiskReason.DAILY_LOSS_LIMIT.value,
        risk_state=RiskState.REDUCE_ONLY,
        risk_reasons=(RiskReason.DAILY_LOSS_LIMIT,),
    )
    audit.append(
        kind=OperationalEventKind.LIVE_PIPELINE_FAULT,
        event_ts_ns=_timestamp_ns(BASE + timedelta(days=4)),
        success=False,
        detail="live pipeline failed closed",
    )
    _rebind(root, "raw/operations/execution-audit.jsonl")
    incident_path = root / "raw/operations/incident-register.json"
    incident_register = ProductionIncidentRegister.model_validate_json(incident_path.read_bytes())
    incident_path.write_bytes(
        incident_register.model_copy(
            update={
                "incidents": (
                    ProductionIncident(
                        incident_id="critical-incident-001",
                        severity=ProductionIncidentSeverity.CRITICAL,
                        started_ts_ns=_timestamp_ns(BASE + timedelta(days=5)),
                        ended_ts_ns=_timestamp_ns(BASE + timedelta(days=5, minutes=30)),
                        resolved=True,
                        evidence_paths=("raw/support/native_rollback.txt",),
                    ),
                )
            }
        ).canonical_bytes()
        + b"\n"
    )
    _rebind(root, "raw/operations/incident-register.json")

    observation = assemble_native_production_observation(
        root,
        policy=_policy(),
        expected_key_id=key_id,
        expected_public_key_sha256=fingerprint,
    )
    assert observation.critical_incidents == 2
    assert observation.reconciliation_failures == 1
    assert observation.risk_breaches == 1


def test_production_contracts_reject_ambiguous_inventory_and_untyped_risk_state(
    tmp_path: Path,
) -> None:
    root, manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    with pytest.raises(ValidationError, match="category references"):
        ProductionEvidenceManifest.model_validate(
            {
                **manifest.model_dump(),
                "artifacts": (
                    *manifest.artifacts,
                    manifest.artifacts[-1].model_copy(
                        update={"relative_path": "raw/support/duplicate.txt"}
                    ),
                ),
            }
        )
    with pytest.raises(ValidationError, match="below raw"):
        ProductionEvidenceArtifact(
            category=ProductionEvidenceCategory.SUPPORTING_EVIDENCE,
            reference_id="traversal",
            relative_path="../secret",
            content_sha256="a" * 64,
            byte_count=1,
            captured_start_ts_ns=1,
            captured_end_ts_ns=2,
        )
    with pytest.raises(ValidationError, match="typed risk reasons"):
        OperationalEvidenceEvent(
            event_id="execution-event-1",
            sequence=1,
            component=AcceptanceComponent.EXECUTION,
            kind=OperationalEventKind.RISK_STATE,
            event_ts_ns=1,
            success=True,
            risk_state=RiskState.REDUCE_ONLY,
            detail="missing typed reason",
        )

    output = (tmp_path / "relative-output.json").relative_to(tmp_path)
    policy_path = _policy_path(tmp_path)
    assert (
        retirement_main(
            [
                "assemble-native",
                "--evidence-root",
                str(root),
                "--policy",
                str(policy_path),
                "--output",
                str(output),
                "--approval-key-id",
                key_id,
                "--approval-public-key-sha256",
                fingerprint,
            ]
        )
        == 2
    )


def test_native_production_assembler_requires_frozen_policy_and_live_audits(
    tmp_path: Path,
) -> None:
    root, _manifest = _bundle(tmp_path / "late-policy")
    key_id, fingerprint = _trust(root)
    with pytest.raises(ValueError, match="policy was not frozen"):
        assemble_native_production_observation(
            root,
            policy=_policy().model_copy(
                update={"frozen_at_ns": _timestamp_ns(OBSERVATION_START + timedelta(seconds=1))}
            ),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )

    root, _manifest = _bundle(tmp_path / "no-reconciliation")
    key_id, fingerprint = _trust(root)
    execution_path = root / "raw/operations/execution-audit.jsonl"
    execution_path.unlink()
    OperationalEvidenceLog(
        execution_path,
        component=AcceptanceComponent.EXECUTION,
    ).append(
        kind=OperationalEventKind.RECONCILIATION,
        event_ts_ns=_timestamp_ns(OBSERVATION_START + timedelta(minutes=1)),
        success=False,
        detail="reconciliation failed closed",
    )
    _rebind(root, "raw/operations/execution-audit.jsonl")
    with pytest.raises(ValueError, match="no successful reconciliation"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )

    root, _manifest = _bundle(tmp_path / "no-deadman")
    key_id, fingerprint = _trust(root)
    sentinel_path = root / "raw/operations/sentinel-audit.jsonl"
    sentinel_path.unlink()
    OperationalEvidenceLog(
        sentinel_path,
        component=AcceptanceComponent.SENTINEL,
    ).append(
        kind=OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
        event_ts_ns=_timestamp_ns(OBSERVATION_START + timedelta(minutes=1)),
        success=True,
        detail="cancel path exercised without a health sample",
    )
    _rebind(root, "raw/operations/sentinel-audit.jsonl")
    with pytest.raises(ValueError, match="no successful dead-man"):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )


def test_native_observation_loader_and_verifier_reject_changed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    observation = assemble_native_production_observation(
        root,
        policy=_policy(),
        expected_key_id=key_id,
        expected_public_key_sha256=fingerprint,
    )
    path = (tmp_path / "observation.json").resolve()
    path.write_bytes(observation.canonical_bytes() + b"\n")
    assert load_native_production_observation(path) == observation
    with pytest.raises(ValueError, match="does not match its evidence bundle"):
        verify_native_production_observation(
            root,
            observation.model_copy(update={"evidence_bundle_sha256": "f" * 64}),
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
    with pytest.raises(ValueError, match="dated after verification"):
        verify_native_production_observation(
            root,
            observation.model_copy(
                update={"assembled_ts_ns": _timestamp_ns(CREATED + timedelta(seconds=1))}
            ),
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
    monkeypatch.setattr(
        collector_module,
        "time_ns",
        lambda: observation.authorization_expires_ts_ns,
    )
    with pytest.raises(ValueError, match="not active at verification"):
        verify_native_production_observation(
            root,
            observation,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
    path.write_bytes(observation.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_native_production_observation(path)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("incident-lineage", "incident register lineage"),
        ("drill-identity", "artifact reference"),
        ("drill-interval", "escapes the production interval"),
        ("drill-checks", "check set is incomplete"),
        ("drill-failed", "drill did not pass"),
        ("drill-unbound-proof", "references unbound evidence"),
        ("drill-control-proof", "cannot use a control report"),
    ),
)
def test_native_production_assembler_rejects_invalid_review_evidence(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    if mutation == "incident-lineage":
        relative = "raw/operations/incident-register.json"
        path = root / relative
        register = ProductionIncidentRegister.model_validate_json(path.read_bytes())
        changed_bytes = register.model_copy(update={"admission_id": "f" * 64}).canonical_bytes()
    else:
        relative = "raw/drills/native_rollback.json"
        path = root / relative
        report = NativeDrillEvidence.model_validate_json(path.read_bytes())
        if mutation == "drill-identity":
            changed = report.model_copy(update={"drill": RequiredNativeDrill.BACKUP_RESTORE})
        elif mutation == "drill-interval":
            changed = report.model_copy(
                update={"started_ts_ns": _timestamp_ns(OBSERVATION_START - timedelta(seconds=1))}
            )
        elif mutation == "drill-checks":
            changed = report.model_copy(update={"checks": report.checks[1:]})
        elif mutation == "drill-failed":
            changed = report.model_copy(
                update={
                    "checks": (
                        report.checks[0].model_copy(update={"passed": False}),
                        *report.checks[1:],
                    )
                }
            )
        elif mutation == "drill-unbound-proof":
            changed = report.model_copy(update={"evidence_paths": ("raw/support/missing.txt",)})
        else:
            changed = report.model_copy(
                update={"evidence_paths": ("raw/drills/backup_restore.json",)}
            )
        changed_bytes = changed.canonical_bytes()
    path.write_bytes(changed_bytes + b"\n")
    _rebind(root, relative)
    with pytest.raises(ValueError, match=expected):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )


def test_production_evidence_file_primitives_fail_closed(tmp_path: Path) -> None:
    relative = Path("relative-evidence")
    with pytest.raises(ValueError, match="must be absolute"):
        _validated_root(relative)

    directory = (tmp_path / "directory").resolve()
    directory.mkdir()
    alias = (tmp_path / "directory-alias").resolve()
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink directory"):
        _validated_root(alias)
    regular_file = (tmp_path / "not-a-directory").resolve()
    regular_file.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="non-symlink directory"):
        _validated_root(regular_file)

    missing = (tmp_path / "missing").resolve()
    with pytest.raises(ValueError, match="cannot open"):
        _read_regular(missing, maximum_bytes=10)
    with pytest.raises(ValueError, match="cannot hash"):
        _sha256_regular(missing)
    empty = (tmp_path / "empty").resolve()
    empty.touch()
    with pytest.raises(ValueError, match="size is invalid"):
        _read_regular(empty, maximum_bytes=10)
    with pytest.raises(ValueError, match="non-empty regular"):
        _sha256_regular(empty)

    with pytest.raises(ValueError, match="cannot be parsed"):
        _load_public_key(b"not a public key")
    private_key = ECC.generate(curve="Ed25519")
    with pytest.raises(ValueError, match="only an Ed25519 public key"):
        _load_public_key(private_key.export_key(format="PEM").encode("ascii"))
    signature = DetachedApprovalSignature(
        key_id=APPROVAL_KEY_ID,
        approval_sha256="0" * 64,
        signature_base64=base64.b64encode(b"x" * 64).decode("ascii"),
    )
    with pytest.raises(ValueError, match="binds different canonical bytes"):
        _verify_detached_signature(
            payload=b"payload",
            payload_sha256="1" * 64,
            signature=signature,
            public_key=private_key.public_key(),
            context="test",
        )


@pytest.mark.parametrize("mutation", ("extra", "symlink", "writable"))
def test_native_production_inventory_rejects_ambiguous_files(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, _manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    if mutation == "extra":
        _write(root / "raw/unbound.txt", b"unexpected\n")
        expected = "inventory mismatch"
    elif mutation == "symlink":
        (root / "raw/unbound-link").symlink_to(root / "raw/support/native_rollback.txt")
        expected = "cannot contain symlinks"
    else:
        target = root / "raw/support/native_rollback.txt"
        target.chmod(target.stat().st_mode | 0o020)
        expected = "group/world writable"
    with pytest.raises(ValueError, match=expected):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )


@pytest.mark.parametrize(
    "mutation",
    ("manifest-newline", "signed-newline", "future-manifest", "expired"),
)
def test_native_production_assembler_rejects_noncanonical_or_expired_authority(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, manifest = _bundle(tmp_path)
    key_id, fingerprint = _trust(root)
    if mutation == "manifest-newline":
        path = root / "production-manifest.json"
        path.write_bytes(manifest.canonical_bytes())
        expected = "control file is not canonical"
    elif mutation == "signed-newline":
        relative = "raw/authorization/deployment-approval.json"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"\n")
        _rebind(root, relative)
        expected = "signed production artifact is not canonical"
    elif mutation == "future-manifest":
        changed = manifest.model_copy(
            update={"created_ts_ns": _timestamp_ns(CREATED + timedelta(seconds=1))}
        )
        (root / "production-manifest.json").write_bytes(changed.canonical_bytes() + b"\n")
        expected = "manifest is dated after assembly"
    else:
        monkeypatch.setattr(
            collector_module,
            "time_ns",
            lambda: _timestamp_ns(BASE + timedelta(days=28)),
        )
        expected = "expired before evidence assembly"
    with pytest.raises(ValueError, match=expected):
        assemble_native_production_observation(
            root,
            policy=_policy(),
            expected_key_id=key_id,
            expected_public_key_sha256=fingerprint,
        )
