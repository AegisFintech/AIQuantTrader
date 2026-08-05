from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import ValidationError

import aiquanttrader.governance.cli as governance_cli
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader.execution.heartbeat import HeartbeatPublisher
from aiquanttrader.governance.approval import (
    ApprovalVerificationError,
    RenewalApprovalPaths,
    verify_deployment_renewal,
)
from aiquanttrader.governance.cli import main as governance_main
from aiquanttrader.governance.ledger import (
    DeploymentAdmissionGuard,
    DeploymentAdmissionLedger,
)
from aiquanttrader.governance.models import (
    DeploymentAdmissionRecord,
    DeploymentArtifactBinding,
    DeploymentArtifactKind,
    DeploymentArtifactManifest,
    DeploymentAuthorizationRenewal,
    DetachedApprovalSignature,
    VerifiedDeploymentAdmission,
    VerifiedDeploymentRenewal,
)
from aiquanttrader.risk.kill_switch import KillSwitchStore

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
COMMIT = "1" * 40
IMAGE = "sha256:" + "2" * 64
ACCOUNT = "0x" + "3" * 40
TRADING = "0x" + "4" * 40
CONTROL = "0x" + "5" * 40
APPROVER_KEY = ECC.generate(curve="Ed25519")
APPROVER_FINGERPRINT = hashlib.sha256(
    APPROVER_KEY.public_key().export_key(format="DER", compress=False)
).hexdigest()


def _verified_admission(
    *,
    stage: PromotionStage,
    deployment_id: str,
    approval_id: str,
    rollback_deployment_id: str,
    prior_approval_id: str | None = None,
    base_time: datetime = NOW,
) -> VerifiedDeploymentAdmission:
    expected_kinds = list(DeploymentArtifactKind)
    if stage is PromotionStage.APPROVED_CANARY:
        expected_kinds.remove(DeploymentArtifactKind.CANARY_EVIDENCE)
    digests = {kind: hashlib.sha256(kind.value.encode()).hexdigest() for kind in expected_kinds}
    bindings = tuple(
        DeploymentArtifactBinding(
            kind=kind,
            relative_path=f"{kind.value}.json",
            content_sha256=digests[kind],
        )
        for kind in expected_kinds
    )
    manifest = DeploymentArtifactManifest(
        deployment_id=deployment_id,
        stage=stage,  # type: ignore[arg-type]
        created_at=base_time - timedelta(hours=1),
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
        artifacts=bindings,
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
        artifact_manifest_sha256=manifest.sha256(),
        dependency_lock_sha256=manifest.dependency_lock_sha256,
        dataset_sha256=manifest.dataset_sha256,
        model_sha256=manifest.model_sha256,
        configuration_sha256=manifest.configuration_sha256,
        feature_schema_sha256=manifest.feature_schema_sha256,
        strategy_config_sha256=manifest.strategy_config_sha256,
        risk_policy_sha256=manifest.risk_policy_sha256,
        shadow_evidence_sha256=manifest.shadow_evidence_sha256,
        testnet_evidence_sha256=manifest.testnet_evidence_sha256,
        canary_evidence_sha256=manifest.canary_evidence_sha256,
        capital_limit_usd=Decimal("1000"),
        rollback_deployment_id=rollback_deployment_id,
        prior_approval_id=prior_approval_id,
        approver="risk-owner",
        approved_at=base_time,
        expires_at=base_time + timedelta(days=1),
    )
    payload = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "artifact_manifest": manifest.model_dump(mode="json"),
        "public_key_sha256": APPROVER_FINGERPRINT,
        "signature_envelope_sha256": "8" * 64,
    }
    return VerifiedDeploymentAdmission.model_validate(
        {
            **payload,
            "admission_id": canonical_sha256(payload),
            "verified_at": (base_time + timedelta(minutes=1)).isoformat(),
        }
    )


def _production_ledger(
    tmp_path: Path,
) -> tuple[
    DeploymentAdmissionLedger,
    VerifiedDeploymentAdmission,
]:
    return _production_ledger_at(tmp_path, base_time=NOW)


def _production_ledger_at(
    tmp_path: Path,
    *,
    base_time: datetime,
) -> tuple[DeploymentAdmissionLedger, VerifiedDeploymentAdmission]:
    ledger = DeploymentAdmissionLedger((tmp_path / "admissions.sqlite3").resolve())
    canary = _verified_admission(
        stage=PromotionStage.APPROVED_CANARY,
        deployment_id="canary-001",
        approval_id="canary-approval-001",
        rollback_deployment_id="safe-000",
        base_time=base_time,
    )
    ledger.admit(
        canary,
        actor="operator",
        reason="canary",
        now=base_time + timedelta(minutes=2),
    )
    production = _verified_admission(
        stage=PromotionStage.PRODUCTION,
        deployment_id="production-001",
        approval_id="production-approval-001",
        rollback_deployment_id=canary.approval.deployment_id,
        prior_approval_id=canary.approval.approval_id,
        base_time=base_time,
    )
    ledger.admit(
        production,
        actor="operator",
        reason="production",
        now=base_time + timedelta(minutes=3),
    )
    return ledger, production


def _signed_renewal(
    root: Path,
    *,
    current: DeploymentAdmissionRecord,
    approved_at: datetime,
    expires_at: datetime,
    key: ECC.EccKey | None = None,
    renewal_id: str = "production-renewal-001",
    **updates: object,
) -> tuple[VerifiedDeploymentRenewal, RenewalApprovalPaths, str, ECC.EccKey]:
    record = current
    authority_values: dict[str, object] = {
        "renewal_id": renewal_id,
        "deployment_id": record.deployment_id,
        "initial_approval_id": record.approval_id,
        "admission_id": record.admission_id,
        "prior_authorization_id": record.authorization_id,
        "account_address": record.account_address,
        "vault_address": record.vault_address,
        "artifact_manifest_sha256": record.artifact_manifest_sha256,
        "configuration_sha256": record.configuration_sha256,
        "image_digest": record.image_digest,
        "capital_limit_usd": record.capital_limit_usd,
        "approver": "risk-owner",
        "approved_at": approved_at,
        "expires_at": expires_at,
    }
    authority_values.update(updates)
    authority = DeploymentAuthorizationRenewal.model_validate(authority_values)
    signing_key = APPROVER_KEY if key is None else key
    envelope = DetachedApprovalSignature(
        key_id="production-approver-001",
        approval_sha256=authority.sha256(),
        signature_base64=base64.b64encode(
            eddsa.new(signing_key, "rfc8032").sign(authority.canonical_bytes())
        ).decode("ascii"),
    )
    root.mkdir(parents=True)
    paths = RenewalApprovalPaths(
        renewal_path=root / "renewal.json",
        signature_path=root / "renewal.sig.json",
        public_key_path=root / "approver.pub",
    )
    paths.renewal_path.write_bytes(authority.canonical_bytes())
    paths.signature_path.write_bytes(envelope.canonical_bytes())
    paths.public_key_path.write_text(
        signing_key.public_key().export_key(format="PEM"), encoding="ascii"
    )
    fingerprint = hashlib.sha256(
        signing_key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()
    verified = verify_deployment_renewal(
        paths=paths,
        current=record,
        expected_key_id=envelope.key_id,
        expected_public_key_sha256=fingerprint,
        now=approved_at + timedelta(minutes=1),
    )
    return verified, paths, fingerprint, signing_key


def test_signed_renewal_extends_same_admission_and_guard_past_original_expiry(
    tmp_path: Path,
) -> None:
    ledger, production = _production_ledger(tmp_path)
    try:
        original = ledger.get(production.approval.deployment_id)
        assert original is not None
        verified, _paths, _fingerprint, _key = _signed_renewal(
            tmp_path / "renewal-1",
            current=original,
            approved_at=NOW + timedelta(hours=12),
            expires_at=NOW + timedelta(days=7),
        )
        renewed = ledger.renew(
            verified,
            actor="release-operator",
            reason="weekly authority review",
            now=NOW + timedelta(hours=13),
        )

        assert renewed.deployment_id == original.deployment_id
        assert renewed.approval_id == original.approval_id
        assert renewed.admission_id == original.admission_id
        assert renewed.artifact_manifest_sha256 == original.artifact_manifest_sha256
        assert renewed.configuration_sha256 == original.configuration_sha256
        assert renewed.image_digest == original.image_digest
        assert renewed.capital_limit_usd == original.capital_limit_usd
        assert renewed.authorization_id == verified.authorization_id
        assert renewed.renewal_count == 1
        assert renewed.expires_at == NOW + timedelta(days=7)
        assert ledger.renewal_history(renewed.deployment_id) == (verified,)

        guard = DeploymentAdmissionGuard(ledger, production)
        assert guard.require_active(now=NOW + timedelta(days=2)) == renewed
        assert guard.expires_at == renewed.expires_at
        heartbeat = HeartbeatPublisher(
            (tmp_path / "heartbeat.json").resolve(),
            environment="production",
            account_address=ACCOUNT,
            config_fingerprint="9" * 64,
            kill_switch=KillSwitchStore((tmp_path / "kill.json").resolve()),
            admission=production,
            authorization=guard,
        ).publish(now_ns=int((NOW + timedelta(days=2)).timestamp() * 1_000_000_000))
        assert heartbeat.approval_id == original.approval_id
        assert heartbeat.admission_id == original.admission_id
        assert heartbeat.approval_expires_ts_ns == int(
            renewed.expires_at.timestamp() * 1_000_000_000
        )
        with pytest.raises(ValueError, match="expired"):
            guard.require_active(now=renewed.expires_at)
    finally:
        ledger.close()


def test_renewals_are_chained_single_use_and_cannot_change_release_identity(
    tmp_path: Path,
) -> None:
    ledger, production = _production_ledger(tmp_path)
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
        first, paths, fingerprint, key = _signed_renewal(
            tmp_path / "renewal-1",
            current=current,
            approved_at=NOW + timedelta(hours=12),
            expires_at=NOW + timedelta(days=7),
        )
        current = ledger.renew(
            first,
            actor="operator",
            reason="first renewal",
            now=NOW + timedelta(hours=13),
        )
        with pytest.raises(ValueError, match="inactive deployment renewal"):
            ledger.renew(
                first,
                actor="operator",
                reason="expired replay",
                now=first.renewal.expires_at,
            )
        empty = DeploymentAdmissionLedger((tmp_path / "empty.sqlite3").resolve())
        try:
            with pytest.raises(ValueError, match="not registered"):
                empty.renew(
                    first,
                    actor="operator",
                    reason="wrong ledger",
                    now=NOW + timedelta(hours=13),
                )
        finally:
            empty.close()
        with pytest.raises(ApprovalVerificationError, match="prior_authorization_id"):
            verify_deployment_renewal(
                paths=paths,
                current=current,
                expected_key_id="production-approver-001",
                expected_public_key_sha256=fingerprint,
                now=NOW + timedelta(hours=14),
            )
        with pytest.raises(ValueError, match="chain is stale"):
            ledger.renew(
                first,
                actor="operator",
                reason="replay",
                now=NOW + timedelta(hours=14),
            )

        for index, (field, value) in enumerate(
            (
                ("image_digest", "sha256:" + "f" * 64),
                ("configuration_sha256", "e" * 64),
                ("artifact_manifest_sha256", "d" * 64),
                ("capital_limit_usd", Decimal("999")),
                ("account_address", "0x" + "a" * 40),
            )
        ):
            with pytest.raises(ApprovalVerificationError, match=field):
                _signed_renewal(
                    tmp_path / f"mismatch-{index}",
                    current=current,
                    approved_at=NOW + timedelta(days=1),
                    expires_at=NOW + timedelta(days=8),
                    key=key,
                    renewal_id=f"mismatch-{index}",
                    **{field: value},
                )
    finally:
        ledger.close()


def test_renewal_contract_and_verifier_reject_expiry_tampering_and_wrong_trust_root(
    tmp_path: Path,
) -> None:
    ledger, production = _production_ledger(tmp_path)
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
        with pytest.raises(ValidationError, match="seven days"):
            DeploymentAuthorizationRenewal(
                renewal_id="too-long",
                deployment_id=current.deployment_id,
                initial_approval_id=current.approval_id,
                admission_id=current.admission_id,
                prior_authorization_id=current.authorization_id,
                account_address=current.account_address,
                artifact_manifest_sha256=current.artifact_manifest_sha256,
                configuration_sha256=current.configuration_sha256,
                image_digest=current.image_digest,
                capital_limit_usd=current.capital_limit_usd,
                approver="risk-owner",
                approved_at=NOW,
                expires_at=NOW + timedelta(days=8),
            )
        verified, paths, fingerprint, _key = _signed_renewal(
            tmp_path / "renewal",
            current=current,
            approved_at=NOW + timedelta(hours=12),
            expires_at=NOW + timedelta(days=7),
        )
        assert verified.renewal.prior_authorization_id == current.authorization_id
        with pytest.raises(ApprovalVerificationError, match="trust root differs"):
            verify_deployment_renewal(
                paths=paths,
                current=current,
                expected_key_id="production-approver-001",
                expected_public_key_sha256="0" * 64,
                now=NOW + timedelta(hours=13),
            )
        tampered = bytearray(paths.renewal_path.read_bytes())
        tampered[tampered.index(b"risk-owner")] = ord("R")
        paths.renewal_path.write_bytes(tampered)
        with pytest.raises(ApprovalVerificationError, match="binds different"):
            verify_deployment_renewal(
                paths=paths,
                current=current,
                expected_key_id="production-approver-001",
                expected_public_key_sha256=fingerprint,
                now=NOW + timedelta(hours=13),
            )
    finally:
        ledger.close()


def test_renewal_verification_fails_closed_for_state_time_key_and_signature(
    tmp_path: Path,
) -> None:
    ledger, production = _production_ledger(tmp_path)
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
        verified, paths, fingerprint, _key = _signed_renewal(
            tmp_path / "renewal",
            current=current,
            approved_at=NOW + timedelta(hours=12),
            expires_at=NOW + timedelta(days=7),
        )

        def verify_at(
            record: DeploymentAdmissionRecord,
            instant: datetime,
        ) -> VerifiedDeploymentRenewal:
            return verify_deployment_renewal(
                paths=paths,
                current=record,
                expected_key_id="production-approver-001",
                expected_public_key_sha256=fingerprint,
                now=instant,
            )

        with pytest.raises(ApprovalVerificationError, match="timezone-aware"):
            verify_at(current, (NOW + timedelta(hours=13)).replace(tzinfo=None))
        with pytest.raises(ApprovalVerificationError, match="active admission"):
            verify_at(
                current.model_copy(update={"state": "revoked"}),
                NOW + timedelta(hours=13),
            )
        with pytest.raises(ApprovalVerificationError, match="only production"):
            verify_at(
                current.model_copy(update={"stage": PromotionStage.APPROVED_CANARY}),
                NOW + timedelta(hours=13),
            )
        with pytest.raises(ApprovalVerificationError, match="expired"):
            verify_at(current, current.expires_at)
        with pytest.raises(ApprovalVerificationError, match="key identity"):
            verify_deployment_renewal(
                paths=paths,
                current=current,
                expected_key_id="wrong-key",
                expected_public_key_sha256=fingerprint,
                now=NOW + timedelta(hours=13),
            )
        with pytest.raises(ApprovalVerificationError, match="not active"):
            verify_at(current, verified.renewal.approved_at - timedelta(seconds=1))

        envelope = DetachedApprovalSignature.model_validate_json(paths.signature_path.read_bytes())
        paths.signature_path.write_bytes(
            envelope.model_copy(
                update={"signature_base64": base64.b64encode(b"x" * 64).decode("ascii")}
            ).canonical_bytes()
        )
        with pytest.raises(ApprovalVerificationError, match="signature is invalid"):
            verify_at(current, NOW + timedelta(hours=13))
    finally:
        ledger.close()


def test_renewal_models_and_nonextending_windows_are_rejected(tmp_path: Path) -> None:
    ledger, production = _production_ledger(tmp_path)
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
        with pytest.raises(ApprovalVerificationError, match="extend"):
            _signed_renewal(
                tmp_path / "nonextending",
                current=current,
                approved_at=NOW + timedelta(hours=1),
                expires_at=NOW + timedelta(hours=12),
            )
        base = DeploymentAuthorizationRenewal(
            renewal_id="model-check",
            deployment_id=current.deployment_id,
            initial_approval_id=current.approval_id,
            admission_id=current.admission_id,
            prior_authorization_id=current.authorization_id,
            account_address=current.account_address,
            artifact_manifest_sha256=current.artifact_manifest_sha256,
            configuration_sha256=current.configuration_sha256,
            image_digest=current.image_digest,
            capital_limit_usd=current.capital_limit_usd,
            approver="risk-owner",
            approved_at=NOW + timedelta(hours=12),
            expires_at=NOW + timedelta(days=7),
        )
        with pytest.raises(ValidationError, match="timezone-aware"):
            DeploymentAuthorizationRenewal.model_validate(
                {**base.model_dump(), "approved_at": base.approved_at.replace(tzinfo=None)}
            )
        with pytest.raises(ValidationError, match="follow approval"):
            DeploymentAuthorizationRenewal.model_validate(
                {**base.model_dump(), "expires_at": base.approved_at}
            )
        with pytest.raises(ValidationError, match="vault and account"):
            DeploymentAuthorizationRenewal.model_validate(
                {**base.model_dump(), "vault_address": base.account_address}
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            base.is_active(base.approved_at.replace(tzinfo=None))
        with pytest.raises(ValidationError, match="initial deployment authorization"):
            type(current).model_validate({**current.model_dump(), "authorization_id": "f" * 64})
        with pytest.raises(ValidationError, match="renewal identity"):
            type(current).model_validate({**current.model_dump(), "renewal_count": 1})
    finally:
        ledger.close()


def test_v1_ledger_migrates_initial_authorization_without_granting_new_time(
    tmp_path: Path,
) -> None:
    database = (tmp_path / "legacy-admissions.sqlite3").resolve()
    ledger, production = _production_ledger(tmp_path / "source")
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
    finally:
        ledger.close()
    old_payload = current.model_dump(
        mode="json",
        exclude={"authorization_id", "renewal_count", "approval_public_key_sha256"},
    )
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '1');
            CREATE TABLE admissions (
                deployment_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE,
                admission_id TEXT NOT NULL UNIQUE, stage TEXT NOT NULL,
                account_address TEXT NOT NULL, vault_address TEXT, state TEXT NOT NULL,
                expires_at TEXT NOT NULL, record_json TEXT NOT NULL
            );
            CREATE TABLE transitions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                deployment_id TEXT NOT NULL REFERENCES admissions(deployment_id),
                occurred_at TEXT NOT NULL, previous_state TEXT, next_state TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO admissions(
                deployment_id, approval_id, admission_id, stage, account_address,
                vault_address, state, expires_at, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current.deployment_id,
                current.approval_id,
                current.admission_id,
                current.stage.value,
                current.account_address,
                current.vault_address,
                current.state.value,
                current.expires_at.isoformat(),
                json.dumps(old_payload),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    migrated = DeploymentAdmissionLedger(database)
    try:
        record = migrated.get(current.deployment_id)
        assert record is not None
        assert record.authorization_id == record.admission_id
        assert record.renewal_count == 0
        assert record.approval_public_key_sha256 is None
        assert record.expires_at == current.expires_at
        assert migrated.renewal_history(current.deployment_id) == ()
        with pytest.raises(ApprovalVerificationError, match="no renewal trust root"):
            verify_deployment_renewal(
                paths=RenewalApprovalPaths(database, database, database),
                current=record,
                expected_key_id="unavailable",
                expected_public_key_sha256=APPROVER_FINGERPRINT,
                now=record.admitted_at + timedelta(minutes=1),
            )
    finally:
        migrated.close()


def test_governance_cli_canonicalizes_verifies_and_applies_renewal(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = (tmp_path / "state").resolve()
    actual_now = datetime.now(UTC)
    ledger, production = _production_ledger_at(
        state_root / "governance",
        base_time=actual_now - timedelta(hours=1),
    )
    try:
        current = ledger.get(production.approval.deployment_id)
        assert current is not None
    finally:
        ledger.close()
    verified, paths, fingerprint, _key = _signed_renewal(
        tmp_path / "renewal-cli",
        current=current,
        approved_at=actual_now - timedelta(minutes=1),
        expires_at=actual_now + timedelta(days=6),
    )
    pretty = tmp_path / "renewal-pretty.json"
    canonical = tmp_path / "renewal-canonical.json"
    pretty.write_text(verified.renewal.model_dump_json(indent=2), encoding="utf-8")
    assert (
        governance_main(
            [
                "canonicalize-renewal",
                "--input",
                str(pretty),
                "--output",
                str(canonical),
            ]
        )
        == 0
    )
    assert canonical.read_bytes() == verified.renewal.canonical_bytes()

    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(state_root))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str((tmp_path / "data").resolve()))
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__PUBLIC_KEY_ID", "production-approver-001")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256", fingerprint)
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__DEPLOYMENT_ID", current.deployment_id)
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__APPROVAL_ID", "canary-approval-001")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__SCALE_APPROVAL_ID", current.approval_id)
    monkeypatch.setenv(
        "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256",
        current.artifact_manifest_sha256,
    )
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__APPROVAL_PATH", "/run/approvals/approval.json")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__MANIFEST_PATH", "/run/approvals/manifest.json")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__SIGNATURE_PATH", "/run/approvals/approval.sig")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__PUBLIC_KEY_PATH", "/run/approvals/approver.pub")
    monkeypatch.setenv("AQT_NATIVE__APPROVAL__ARTIFACT_ROOT_PATH", "/run/approvals/artifacts")
    monkeypatch.setattr(
        governance_cli,
        "verify_deployment_admission",
        lambda *_args, **_kwargs: production,
    )
    common = [
        "--config-dir",
        str(config_dir),
        "--environment",
        "production",
        "--code-identity",
        COMMIT,
        "--image-identity",
        IMAGE,
        "--dependency-lock-path",
        str((tmp_path / "uv.lock").resolve()),
        "--deployment-id",
        current.deployment_id,
        "--renewal",
        str(canonical),
        "--signature",
        str(paths.signature_path),
        "--public-key",
        str(paths.public_key_path),
    ]
    assert governance_main(["verify-renewal", *common]) == 0
    assert verified.authorization_id in capsys.readouterr().out
    assert (
        governance_main(
            [
                "renew",
                *common,
                "--actor",
                "release-operator",
                "--reason",
                "reviewed weekly authority",
            ]
        )
        == 0
    )
    renewed_output = capsys.readouterr().out
    assert '"renewal_count":1' in renewed_output
    assert verified.authorization_id in renewed_output
