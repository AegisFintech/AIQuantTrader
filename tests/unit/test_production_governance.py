from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import ValidationError

import aiquanttrader.governance.cli as governance_cli
from aiquanttrader.config import load_config
from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.domain.base import canonical_json_bytes, canonical_sha256
from aiquanttrader.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader.governance.approval import (
    ApprovalArtifactPaths,
    ApprovalVerificationError,
    configured_artifact_paths,
    verify_deployment_admission,
)
from aiquanttrader.governance.cli import main as governance_main
from aiquanttrader.governance.evidence import (
    evaluate_canary_evidence,
    load_canary_policy,
)
from aiquanttrader.governance.ledger import (
    DeploymentAdmissionGuard,
    DeploymentAdmissionLedger,
)
from aiquanttrader.governance.models import (
    CanaryEvidencePolicy,
    CanaryEvidenceReport,
    CanaryGateResult,
    CanaryObservation,
    DeploymentAdmissionRecord,
    DeploymentAdmissionState,
    DeploymentArtifactBinding,
    DeploymentArtifactKind,
    DeploymentArtifactManifest,
    DetachedApprovalSignature,
    VerifiedDeploymentAdmission,
)
from aiquanttrader.risk.authority import limits_sha

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)
COMMIT = "b" * 40
IMAGE = "sha256:" + "c" * 64
ACCOUNT = "0x" + "1" * 40
TRADING = "0x" + "2" * 40
CONTROL = "0x" + "3" * 40


def _mainnet_environment() -> dict[str, str]:
    return {
        "AQT_NATIVE__APPROVAL__DEPLOYMENT_ID": "deployment-canary-001",
        "AQT_NATIVE__APPROVAL__APPROVAL_ID": "approval-canary-001",
        "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256": "a" * 64,
        "AQT_NATIVE__APPROVAL__APPROVAL_PATH": "/run/approvals/approval.json",
        "AQT_NATIVE__APPROVAL__MANIFEST_PATH": "/run/approvals/manifest.json",
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_PATH": "/run/approvals/approver.pub",
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_ID": "approver-001",
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256": "a" * 64,
        "AQT_NATIVE__APPROVAL__SIGNATURE_PATH": "/run/approvals/approval.sig.json",
        "AQT_NATIVE__APPROVAL__ARTIFACT_ROOT_PATH": "/run/approvals/artifacts",
        "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
        "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": ("/run/secrets/mainnet-control-wallet"),
        "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": ("/run/secrets/mainnet-trading-wallet"),
        "AQT_NATIVE__EXECUTION__ENABLED": "true",
        "AQT_NATIVE__SENTINEL__ENABLED": "true",
        "AQT_NATIVE__RISK__MAX_ORDER_SIZE_BASE": "0.002",
        "AQT_NATIVE__RISK__MAX_POSITION_SIZE_BASE": "0.01",
        "AQT_NATIVE__RISK__MAX_ORDER_NOTIONAL_USD": "100",
        "AQT_NATIVE__RISK__MAX_INVENTORY_NOTIONAL_USD": "500",
        "AQT_NATIVE__RISK__MAX_OPEN_ORDERS": "2",
        "AQT_NATIVE__RISK__MAX_ORDERS_PER_SECOND": "2",
    }


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _signed_canary(
    tmp_path: Path,
    config_dir: Path,
) -> tuple[VerifiedDeploymentAdmission, ApprovalArtifactPaths]:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    payloads = {
        DeploymentArtifactKind.DEPENDENCY_LOCK: b"exact dependency lock\n",
        DeploymentArtifactKind.DATASET_MANIFEST: b'{"dataset":"btc"}\n',
        DeploymentArtifactKind.MODEL_MANIFEST: b'{"model":"none"}\n',
        DeploymentArtifactKind.FEATURE_SCHEMA: b'{"schema":1}\n',
        DeploymentArtifactKind.STRATEGY_CONFIG: b"strategy = 'scalper'\n",
        DeploymentArtifactKind.RISK_POLICY: b"max_inventory = 500\n",
        DeploymentArtifactKind.SHADOW_EVIDENCE: b'{"verdict":"passed"}\n',
        DeploymentArtifactKind.TESTNET_EVIDENCE: b'{"verdict":"passed"}\n',
    }
    bindings: list[DeploymentArtifactBinding] = []
    for kind, payload in payloads.items():
        relative = "uv.lock" if kind is DeploymentArtifactKind.DEPENDENCY_LOCK else f"{kind}.json"
        (artifact_root / relative).write_bytes(payload)
        bindings.append(
            DeploymentArtifactBinding(
                kind=kind,
                relative_path=relative,
                content_sha256=_sha(payload),
            )
        )

    key = ECC.generate(curve="Ed25519")
    public_pem = key.public_key().export_key(format="PEM").encode("ascii")
    public_sha = _sha(key.public_key().export_key(format="DER", compress=False))
    environment = _mainnet_environment()
    environment["AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256"] = public_sha
    environment["AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS"] = ACCOUNT
    bundle = load_config(config_dir, "canary", environ=environment)
    risk_payload = canonical_json_bytes(bundle.settings.risk.model_dump(mode="json"))
    risk_relative = f"{DeploymentArtifactKind.RISK_POLICY}.json"
    (artifact_root / risk_relative).write_bytes(risk_payload)
    bindings = [
        DeploymentArtifactBinding(
            kind=binding.kind,
            relative_path=binding.relative_path,
            content_sha256=_sha(risk_payload),
        )
        if binding.kind is DeploymentArtifactKind.RISK_POLICY
        else binding
        for binding in bindings
    ]
    by_kind = {binding.kind: binding.content_sha256 for binding in bindings}
    manifest = DeploymentArtifactManifest(
        deployment_id="deployment-canary-001",
        stage=PromotionStage.APPROVED_CANARY,
        created_at=NOW - timedelta(hours=1),
        commit_sha=COMMIT,
        image_digest=IMAGE,
        configuration_sha256=bundle.settings.approval_configuration_fingerprint(),
        dependency_lock_sha256=by_kind[DeploymentArtifactKind.DEPENDENCY_LOCK],
        dataset_sha256=by_kind[DeploymentArtifactKind.DATASET_MANIFEST],
        model_sha256=by_kind[DeploymentArtifactKind.MODEL_MANIFEST],
        feature_schema_sha256=by_kind[DeploymentArtifactKind.FEATURE_SCHEMA],
        strategy_config_sha256=by_kind[DeploymentArtifactKind.STRATEGY_CONFIG],
        risk_policy_sha256=limits_sha(bundle.settings.risk),
        shadow_evidence_sha256=by_kind[DeploymentArtifactKind.SHADOW_EVIDENCE],
        testnet_evidence_sha256=by_kind[DeploymentArtifactKind.TESTNET_EVIDENCE],
        rollback_deployment_id="deployment-safe-000",
        artifacts=tuple(bindings),
    )
    environment["AQT_NATIVE__APPROVAL__DEPLOYMENT_ID"] = manifest.deployment_id
    environment["AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256"] = manifest.sha256()
    bundle = load_config(config_dir, "canary", environ=environment)
    approval = DeploymentApproval(
        approval_id="approval-canary-001",
        deployment_id=manifest.deployment_id,
        stage=PromotionStage.APPROVED_CANARY,
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
        capital_limit_usd=Decimal("1000"),
        rollback_deployment_id=manifest.rollback_deployment_id,
        approver="risk-owner@example.invalid",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    signature_bytes = eddsa.new(key, "rfc8032").sign(approval.canonical_bytes())
    signature = DetachedApprovalSignature(
        key_id="approver-001",
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(signature_bytes).decode("ascii"),
    )
    approval_path = tmp_path / "approval.json"
    manifest_path = tmp_path / "manifest.json"
    signature_path = tmp_path / "signature.json"
    public_path = tmp_path / "public.pem"
    approval_path.write_bytes(approval.canonical_bytes())
    manifest_path.write_bytes(manifest.canonical_bytes())
    signature_path.write_bytes(signature.canonical_bytes())
    public_path.write_bytes(public_pem)
    paths = ApprovalArtifactPaths(
        approval_path=approval_path,
        manifest_path=manifest_path,
        signature_path=signature_path,
        public_key_path=public_path,
        artifact_root=artifact_root,
        runtime_dependency_lock_path=artifact_root / "uv.lock",
    )
    admission = verify_deployment_admission(
        bundle,
        paths,
        code_identity=COMMIT,
        image_identity=IMAGE,
        now=NOW + timedelta(minutes=1),
    )
    return admission, paths


def _production_admission(canary: VerifiedDeploymentAdmission) -> VerifiedDeploymentAdmission:
    canary_evidence = "d" * 64
    artifact = DeploymentArtifactBinding(
        kind=DeploymentArtifactKind.CANARY_EVIDENCE,
        relative_path="canary-evidence.json",
        content_sha256=canary_evidence,
    )
    manifest_values = canary.artifact_manifest.model_dump(mode="json")
    manifest_values.update(
        {
            "deployment_id": "deployment-production-001",
            "stage": PromotionStage.PRODUCTION,
            "canary_evidence_sha256": canary_evidence,
            "rollback_deployment_id": canary.approval.deployment_id,
            "artifacts": [
                *manifest_values["artifacts"],
                artifact.model_dump(mode="json"),
            ],
        }
    )
    manifest = DeploymentArtifactManifest.model_validate(manifest_values)
    approval_values = canary.approval.model_dump(mode="json")
    approval_values.update(
        {
            "approval_id": "approval-production-001",
            "deployment_id": manifest.deployment_id,
            "stage": PromotionStage.PRODUCTION,
            "artifact_manifest_sha256": manifest.sha256(),
            "canary_evidence_sha256": canary_evidence,
            "rollback_deployment_id": canary.approval.deployment_id,
            "prior_approval_id": canary.approval.approval_id,
        }
    )
    approval = DeploymentApproval.model_validate(approval_values)
    payload = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "artifact_manifest": manifest.model_dump(mode="json"),
        "public_key_sha256": canary.public_key_sha256,
        "signature_envelope_sha256": canary.signature_envelope_sha256,
    }
    return VerifiedDeploymentAdmission.model_validate(
        {
            **payload,
            "admission_id": canonical_sha256(payload),
            "verified_at": (NOW + timedelta(minutes=2)).isoformat(),
        }
    )


def _bundle_for_admission(
    config_dir: Path,
    admission: VerifiedDeploymentAdmission,
    **updates: str,
) -> ConfigBundle:
    environment = {
        **_mainnet_environment(),
        "AQT_NATIVE__APPROVAL__DEPLOYMENT_ID": admission.approval.deployment_id,
        "AQT_NATIVE__APPROVAL__APPROVAL_ID": admission.approval.approval_id,
        "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256": (admission.artifact_manifest.sha256()),
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256": admission.public_key_sha256,
        "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
    }
    environment.update(updates)
    return load_config(config_dir, "canary", environ=environment)


def test_signed_admission_is_stable_and_detects_tampering(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, paths = _signed_canary(tmp_path, config_dir)
    later = verify_deployment_admission(
        _bundle_for_admission(config_dir, admission),
        paths,
        code_identity=COMMIT,
        image_identity=IMAGE,
        now=NOW + timedelta(minutes=2),
        wallet_role="trading",
        wallet_address=TRADING,
    )

    assert later.admission_id == admission.admission_id
    assert later.verified_at != admission.verified_at
    with pytest.raises(ApprovalVerificationError, match="trading wallet"):
        verify_deployment_admission(
            _bundle_for_admission(config_dir, admission),
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=2),
            wallet_role="trading",
            wallet_address=CONTROL,
        )
    bound = paths.artifact_root / "dataset_manifest.json"
    bound.write_bytes(b"tampered")
    with pytest.raises(ApprovalVerificationError, match="content mismatch"):
        verify_deployment_admission(
            _bundle_for_admission(config_dir, admission),
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=2),
        )


def test_runtime_may_reverify_expired_identity_only_for_durable_ledger_guard(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, paths = _signed_canary(tmp_path, config_dir)
    after_expiry = admission.approval.expires_at + timedelta(minutes=1)
    with pytest.raises(ApprovalVerificationError, match="not active"):
        verify_deployment_admission(
            _bundle_for_admission(config_dir, admission),
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=after_expiry,
        )

    historical_identity = verify_deployment_admission(
        _bundle_for_admission(config_dir, admission),
        paths,
        code_identity=COMMIT,
        image_identity=IMAGE,
        now=after_expiry,
        require_active_approval=False,
    )
    assert historical_identity.admission_id == admission.admission_id

    ledger = DeploymentAdmissionLedger((tmp_path / "admissions.sqlite3").resolve())
    try:
        ledger.admit(
            admission,
            actor="operator",
            reason="admitted while approval active",
            now=NOW + timedelta(minutes=2),
        )
        guard = DeploymentAdmissionGuard(ledger, historical_identity)
        with pytest.raises(ValueError, match="expired"):
            guard.require_active(now=after_expiry)
    finally:
        ledger.close()


def test_ledger_requires_canary_predecessor_and_blocks_replay(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary, _paths = _signed_canary(tmp_path, config_dir)
    ledger_path = (tmp_path / "state" / "governance" / "admissions.sqlite3").resolve()
    ledger = DeploymentAdmissionLedger(ledger_path)
    try:
        canary_record = ledger.admit(
            canary,
            actor="operator",
            reason="minimum capital canary",
            now=NOW + timedelta(minutes=1),
        )
        production = _production_admission(canary)
        production_record = ledger.admit(
            production,
            actor="risk-owner",
            reason="separate scale approval",
            now=NOW + timedelta(minutes=3),
        )
        assert production_record.state is DeploymentAdmissionState.ACTIVE
        predecessor = ledger.get(canary_record.deployment_id)
        assert predecessor is not None
        assert predecessor.state is DeploymentAdmissionState.SUPERSEDED
    finally:
        ledger.close()

    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str((tmp_path / "state").resolve()))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str((tmp_path / "data").resolve()))
    common = ["--config-dir", str(config_dir), "--environment", "canary"]
    assert governance_main(["status", *common]) == 0
    assert production_record.deployment_id in capsys.readouterr().out
    assert (
        governance_main(
            [
                "rollback",
                *common,
                "--deployment-id",
                production_record.deployment_id,
                "--actor",
                "operator",
                "--reason",
                "rollback drill",
            ]
        )
        == 0
    )
    assert '"state":"rolled_back"' in capsys.readouterr().out

    ledger = DeploymentAdmissionLedger(ledger_path)
    try:
        with pytest.raises(ValueError, match="consumed"):
            ledger.admit(
                production,
                actor="operator",
                reason="replay",
                now=NOW + timedelta(minutes=4),
            )
    finally:
        ledger.close()

    reader = DeploymentAdmissionLedger(ledger_path, read_only=True)
    try:
        guard = DeploymentAdmissionGuard(reader, production)
        assert not guard.is_active(now=NOW + timedelta(minutes=4))
    finally:
        reader.close()


def test_canary_evidence_passes_gates_but_requires_new_approval(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, _paths = _signed_canary(tmp_path, config_dir)
    ledger = DeploymentAdmissionLedger((tmp_path / "ledger.sqlite3").resolve())
    try:
        record = ledger.admit(
            admission,
            actor="operator",
            reason="canary",
            now=NOW + timedelta(minutes=1),
        )
    finally:
        ledger.close()
    policy = CanaryEvidencePolicy(
        policy_id="canary-v1",
        frozen_at_ns=1,
        minimum_observation_ns=10,
        minimum_orders=10,
        minimum_fills=5,
        minimum_maker_fills=4,
        maximum_drawdown_fraction=Decimal("0.01"),
        maximum_rejection_fraction=Decimal("0.1"),
        maximum_adverse_markout_bps=Decimal("5"),
        required_drills=(
            "operator_kill",
            "deadman_expiry",
            "restart_reconciliation",
            "credential_rotation",
            "backup_restore",
        ),
    )
    observation = CanaryObservation(
        deployment_id=record.deployment_id,
        admission_id=record.admission_id,
        started_ts_ns=1,
        ended_ts_ns=20,
        orders=10,
        fills=5,
        maker_fills=4,
        rejected_orders=1,
        unknown_outcomes=0,
        reconciliation_failures=0,
        fee_events=5,
        funding_events=1,
        post_cost_pnl_usd=Decimal("1"),
        maximum_drawdown_fraction=Decimal("0.001"),
        mean_adverse_markout_bps=Decimal("-1"),
        maximum_account_equity_usd=Decimal("1000"),
        completed_drills=policy.required_drills,
        evidence_bundle_sha256="e" * 64,
    )

    report = evaluate_canary_evidence(
        admission=record,
        observation=observation,
        policy=policy,
        generated_ts_ns=21,
    )

    assert report.awaiting_production_approval
    assert all(gate.passed for gate in report.gates)

    with pytest.raises(ValueError, match="active admitted"):
        evaluate_canary_evidence(
            admission=record.model_copy(update={"state": DeploymentAdmissionState.REVOKED}),
            observation=observation,
            policy=policy,
        )
    with pytest.raises(ValueError, match="deployment does not match"):
        evaluate_canary_evidence(
            admission=record,
            observation=observation.model_copy(update={"deployment_id": "different"}),
            policy=policy,
        )
    with pytest.raises(ValueError, match="admission identity"):
        evaluate_canary_evidence(
            admission=record,
            observation=observation.model_copy(update={"admission_id": "f" * 64}),
            policy=policy,
        )


def test_approval_canonicalization_emits_exact_signable_bytes(
    tmp_path: Path,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admission, _paths = _signed_canary(tmp_path, config_dir)
    source = tmp_path / "pretty-approval.json"
    output = tmp_path / "canonical-approval.json"
    source.write_text(admission.approval.model_dump_json(indent=2), encoding="utf-8")

    assert (
        governance_main(
            [
                "canonicalize-approval",
                "--input",
                str(source),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == admission.approval.canonical_bytes()
    assert admission.approval.sha256() in capsys.readouterr().out


def test_governance_cli_requires_explicit_admission_and_deactivation(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admission, paths = _signed_canary(tmp_path, config_dir)
    active_now = datetime.now(UTC)
    active_approval = admission.approval.model_copy(
        update={
            "approved_at": active_now - timedelta(minutes=1),
            "expires_at": active_now + timedelta(days=1),
        }
    )
    identity = {
        "schema_version": 1,
        "approval": active_approval.model_dump(mode="json"),
        "artifact_manifest": admission.artifact_manifest.model_dump(mode="json"),
        "public_key_sha256": admission.public_key_sha256,
        "signature_envelope_sha256": admission.signature_envelope_sha256,
    }
    active_admission = VerifiedDeploymentAdmission.model_validate(
        {
            **identity,
            "admission_id": canonical_sha256(identity),
            "verified_at": active_now.isoformat(),
        }
    )
    environment = {
        **_mainnet_environment(),
        "AQT_NATIVE__APPROVAL__ARTIFACT_MANIFEST_SHA256": (
            active_admission.artifact_manifest.sha256()
        ),
        "AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256": active_admission.public_key_sha256,
        "AQT_NATIVE__STORAGE__STATE_ROOT": str((tmp_path / "state").resolve()),
        "AQT_NATIVE__STORAGE__DATA_ROOT": str((tmp_path / "data").resolve()),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        governance_cli,
        "verify_deployment_admission",
        lambda *_args, **_kwargs: active_admission,
    )
    release = [
        "--config-dir",
        str(config_dir),
        "--environment",
        "canary",
        "--code-identity",
        COMMIT,
        "--image-identity",
        IMAGE,
        "--dependency-lock-path",
        str(paths.runtime_dependency_lock_path),
    ]

    assert governance_main(["verify", *release]) == 0
    assert active_admission.admission_id in capsys.readouterr().out
    assert (
        governance_main(["admit", *release, "--actor", "operator", "--reason", "bounded canary"])
        == 0
    )
    assert '"state":"active"' in capsys.readouterr().out

    observation = CanaryObservation(
        deployment_id=active_admission.approval.deployment_id,
        admission_id=active_admission.admission_id,
        started_ts_ns=1,
        ended_ts_ns=2,
        orders=0,
        fills=0,
        maker_fills=0,
        rejected_orders=0,
        unknown_outcomes=0,
        reconciliation_failures=0,
        fee_events=0,
        funding_events=0,
        post_cost_pnl_usd=Decimal("0"),
        maximum_drawdown_fraction=Decimal("0"),
        mean_adverse_markout_bps=Decimal("0"),
        maximum_account_equity_usd=Decimal("1000"),
        completed_drills=(),
        evidence_bundle_sha256="e" * 64,
    )
    observation_path = tmp_path / "observation.json"
    report_path = tmp_path / "canary-report.json"
    observation_path.write_bytes(observation.canonical_bytes())
    common = ["--config-dir", str(config_dir), "--environment", "canary"]
    assert (
        governance_main(
            [
                "evaluate-canary",
                *common,
                "--deployment-id",
                active_admission.approval.deployment_id,
                "--observation",
                str(observation_path),
                "--policy",
                str(config_dir / "production" / "canary-evidence-v1.toml"),
                "--output",
                str(report_path),
            ]
        )
        == 1
    )
    assert report_path.is_file()
    assert not CanaryEvidenceReport.model_validate_json(
        report_path.read_bytes()
    ).awaiting_production_approval
    capsys.readouterr()

    assert governance_main(["status", *common, "--deployment-id", "missing"]) == 0
    assert capsys.readouterr().out.strip() == "null"
    assert (
        governance_main(
            [
                "revoke",
                *common,
                "--deployment-id",
                active_admission.approval.deployment_id,
                "--actor",
                "operator",
                "--reason",
                "completed canary",
            ]
        )
        == 0
    )
    assert '"state":"revoked"' in capsys.readouterr().out
    assert (
        governance_main(
            [
                "evaluate-canary",
                *common,
                "--deployment-id",
                "missing",
                "--observation",
                str(observation_path),
                "--policy",
                str(config_dir / "production" / "canary-evidence-v1.toml"),
            ]
        )
        == 2
    )
    assert "not registered" in capsys.readouterr().err


def test_approval_verifier_rejects_unsafe_files_and_identity_mismatches(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, paths = _signed_canary(tmp_path, config_dir)
    bundle = _bundle_for_admission(config_dir, admission)

    with pytest.raises(ApprovalVerificationError, match="cannot open"):
        verify_deployment_admission(
            bundle,
            replace(paths, approval_path=tmp_path / "missing.json"),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    empty = tmp_path / "empty.json"
    empty.touch()
    with pytest.raises(ApprovalVerificationError, match="size is invalid"):
        verify_deployment_admission(
            bundle,
            replace(paths, approval_path=empty),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ApprovalVerificationError, match="not regular"):
        verify_deployment_admission(
            bundle,
            replace(paths, approval_path=tmp_path),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )

    invalid_key = tmp_path / "invalid.pub"
    invalid_key.write_bytes(b"not a public key")
    with pytest.raises(ApprovalVerificationError, match="cannot be parsed"):
        verify_deployment_admission(
            bundle,
            replace(paths, public_key_path=invalid_key),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    private_key = tmp_path / "private.pem"
    private_key.write_text(
        ECC.generate(curve="Ed25519").export_key(format="PEM"),
        encoding="ascii",
    )
    with pytest.raises(ApprovalVerificationError, match="must be an Ed25519 public key"):
        verify_deployment_admission(
            bundle,
            replace(paths, public_key_path=private_key),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )

    original_signature = paths.signature_path.read_bytes()
    signature = DetachedApprovalSignature.model_validate_json(original_signature)
    paths.signature_path.write_bytes(
        signature.model_copy(update={"key_id": "different-key"}).canonical_bytes()
    )
    with pytest.raises(ApprovalVerificationError, match="key identity"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    paths.signature_path.write_bytes(
        signature.model_copy(update={"approval_sha256": "f" * 64}).canonical_bytes()
    )
    with pytest.raises(ApprovalVerificationError, match="binds different bytes"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    paths.signature_path.write_bytes(original_signature)

    with pytest.raises(ApprovalVerificationError, match="mismatch: commit_sha"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity="a" * 40,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ApprovalVerificationError, match="root is not a directory"):
        verify_deployment_admission(
            bundle,
            replace(paths, artifact_root=paths.approval_path),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )


def test_approval_verifier_rejects_trust_time_signature_and_lock_failures(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, paths = _signed_canary(tmp_path, config_dir)
    bundle = _bundle_for_admission(config_dir, admission)

    with pytest.raises(ApprovalVerificationError, match="timezone-aware"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=datetime(2026, 8, 4, 12, 1),
        )
    with pytest.raises(ApprovalVerificationError, match="supplied together"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
            wallet_role="control",
        )
    with pytest.raises(ApprovalVerificationError, match="public key fingerprint"):
        verify_deployment_admission(
            _bundle_for_admission(
                config_dir,
                admission,
                AQT_NATIVE__APPROVAL__PUBLIC_KEY_SHA256="f" * 64,
            ),
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ApprovalVerificationError, match="not active"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(days=2),
        )

    original_signature = paths.signature_path.read_bytes()
    signature = DetachedApprovalSignature.model_validate_json(original_signature)
    forged = signature.model_copy(
        update={"signature_base64": base64.b64encode(b"\x00" * 64).decode("ascii")}
    )
    paths.signature_path.write_bytes(forged.canonical_bytes())
    with pytest.raises(ApprovalVerificationError, match="signature is invalid"):
        verify_deployment_admission(
            bundle,
            paths,
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )
    paths.signature_path.write_bytes(original_signature)

    wrong_lock = tmp_path / "wrong.lock"
    wrong_lock.write_bytes(b"different dependency graph")
    with pytest.raises(ApprovalVerificationError, match="runtime dependency lock"):
        verify_deployment_admission(
            bundle,
            ApprovalArtifactPaths(
                approval_path=paths.approval_path,
                manifest_path=paths.manifest_path,
                signature_path=paths.signature_path,
                public_key_path=paths.public_key_path,
                artifact_root=paths.artifact_root,
                runtime_dependency_lock_path=wrong_lock,
            ),
            code_identity=COMMIT,
            image_identity=IMAGE,
            now=NOW + timedelta(minutes=1),
        )


def test_configured_paths_and_canary_policy_are_strict(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    with pytest.raises(ApprovalVerificationError, match="incomplete"):
        configured_artifact_paths(
            load_config(config_dir, "canary", environ={}),
            runtime_dependency_lock_path=tmp_path / "uv.lock",
        )
    admission, _paths = _signed_canary(tmp_path, config_dir)
    configured = configured_artifact_paths(
        _bundle_for_admission(config_dir, admission),
        runtime_dependency_lock_path=tmp_path / "uv.lock",
    )
    assert configured.approval_path == Path("/run/approvals/approval.json")

    policy = load_canary_policy(config_dir / "production" / "canary-evidence-v1.toml")
    assert policy.policy_id == "btc-mainnet-canary-v1"
    empty = tmp_path / "empty-policy.toml"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="invalid"):
        load_canary_policy(empty)


def test_governance_contracts_reject_ambiguous_or_inconsistent_state(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, _paths = _signed_canary(tmp_path, config_dir)

    with pytest.raises(ValidationError, match="cannot traverse"):
        DeploymentArtifactBinding(
            kind=DeploymentArtifactKind.MODEL_MANIFEST,
            relative_path="../model.json",
            content_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="canonical base64"):
        DetachedApprovalSignature(
            key_id="key-1",
            approval_sha256="a" * 64,
            signature_base64="!" * 88,
        )
    with pytest.raises(ValidationError, match="one canonical Ed25519 signature"):
        DetachedApprovalSignature(
            key_id="key-1",
            approval_sha256="a" * 64,
            signature_base64=base64.b64encode(b"x" * 66).decode("ascii"),
        )

    approval_values = admission.approval.model_dump(mode="json")
    with pytest.raises(ValidationError, match="seven days"):
        DeploymentApproval.model_validate(
            {
                **approval_values,
                "expires_at": (NOW + timedelta(days=8)).isoformat(),
            }
        )
    with pytest.raises(ValidationError, match="different"):
        DeploymentApproval.model_validate(
            {
                **approval_values,
                "control_wallet_address": approval_values["trading_wallet_address"],
            }
        )
    with pytest.raises(ValidationError, match="cannot claim prior"):
        DeploymentApproval.model_validate({**approval_values, "prior_approval_id": "unexpected"})
    with pytest.raises(ValidationError, match="requires prior approval"):
        DeploymentApproval.model_validate({**approval_values, "stage": PromotionStage.PRODUCTION})

    manifest_values = admission.artifact_manifest.model_dump(mode="json")
    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        DeploymentArtifactManifest.model_validate(
            {**manifest_values, "created_at": datetime(2026, 8, 4, 11)}
        )
    with pytest.raises(ValidationError, match="rollback target must differ"):
        DeploymentArtifactManifest.model_validate(
            {
                **manifest_values,
                "rollback_deployment_id": manifest_values["deployment_id"],
            }
        )
    with pytest.raises(ValidationError, match="must be unique"):
        DeploymentArtifactManifest.model_validate(
            {
                **manifest_values,
                "artifacts": [
                    *manifest_values["artifacts"],
                    manifest_values["artifacts"][0],
                ],
            }
        )
    with pytest.raises(ValidationError, match="requires canary evidence"):
        DeploymentArtifactManifest.model_validate(
            {**manifest_values, "stage": PromotionStage.PRODUCTION}
        )
    with pytest.raises(ValidationError, match="identity does not match"):
        VerifiedDeploymentAdmission.model_validate(
            {**admission.model_dump(mode="json"), "admission_id": "f" * 64}
        )
    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        VerifiedDeploymentAdmission.model_validate(
            {**admission.model_dump(mode="json"), "verified_at": datetime(2026, 8, 4, 12)}
        )

    with pytest.raises(ValidationError, match="must be unique"):
        CanaryEvidencePolicy(
            policy_id="bad-policy",
            frozen_at_ns=1,
            minimum_observation_ns=1,
            minimum_orders=1,
            minimum_fills=1,
            minimum_maker_fills=1,
            maximum_drawdown_fraction=Decimal("0.1"),
            maximum_rejection_fraction=Decimal("0.1"),
            maximum_adverse_markout_bps=Decimal("5"),
            required_drills=(
                "operator_kill",
                "operator_kill",
                "restart_reconciliation",
                "credential_rotation",
                "backup_restore",
            ),
        )

    observation_values = {
        "deployment_id": admission.approval.deployment_id,
        "admission_id": admission.admission_id,
        "started_ts_ns": 10,
        "ended_ts_ns": 10,
        "orders": 1,
        "fills": 0,
        "maker_fills": 0,
        "rejected_orders": 0,
        "unknown_outcomes": 0,
        "reconciliation_failures": 0,
        "fee_events": 0,
        "funding_events": 0,
        "post_cost_pnl_usd": Decimal("0"),
        "maximum_drawdown_fraction": Decimal("0"),
        "mean_adverse_markout_bps": Decimal("0"),
        "maximum_account_equity_usd": Decimal("1"),
        "completed_drills": (),
        "evidence_bundle_sha256": "a" * 64,
    }
    with pytest.raises(ValidationError, match="positive interval"):
        CanaryObservation.model_validate(observation_values)
    with pytest.raises(ValidationError, match="do not reconcile"):
        CanaryObservation.model_validate({**observation_values, "ended_ts_ns": 11, "fills": 2})

    gate = CanaryGateResult(gate="orders", passed=True, actual="1", required="1")
    report_payload = {
        "schema_version": 1,
        "deployment_id": admission.approval.deployment_id,
        "admission_id": admission.admission_id,
        "policy_id": "policy-1",
        "policy_sha256": "a" * 64,
        "observation_sha256": "b" * 64,
        "generated_ts_ns": 1,
        "gates": (gate,),
        "awaiting_production_approval": True,
    }
    with pytest.raises(ValidationError, match="identity does not match"):
        CanaryEvidenceReport.model_validate({"report_id": "f" * 64, **report_payload})


def test_ledger_rejects_invalid_transitions_and_read_only_writes(
    tmp_path: Path,
    config_dir: Path,
) -> None:
    admission, _paths = _signed_canary(tmp_path, config_dir)
    with pytest.raises(ValueError, match="absolute"):
        DeploymentAdmissionLedger(Path("relative.sqlite3"))

    path = (tmp_path / "ledger-errors.sqlite3").resolve()
    ledger = DeploymentAdmissionLedger(path)
    try:
        with pytest.raises(ValueError, match="active canary predecessor"):
            ledger.admit(
                _production_admission(admission),
                actor="operator",
                reason="invalid direct production",
                now=NOW + timedelta(minutes=1),
            )
        with pytest.raises(ValueError, match="actor"):
            ledger.admit(
                admission,
                actor="",
                reason="invalid actor",
                now=NOW + timedelta(minutes=1),
            )
        record = ledger.admit(
            admission,
            actor="operator",
            reason="canary",
            now=NOW + timedelta(minutes=1),
        )
        assert (
            ledger.admit(
                admission,
                actor="operator",
                reason="idempotent retry",
                now=NOW + timedelta(minutes=2),
            )
            == record
        )
        with pytest.raises(ValueError, match="only roll back or revoke"):
            ledger.deactivate(
                record.deployment_id,
                target=DeploymentAdmissionState.SUPERSEDED,
                actor="operator",
                reason="invalid target",
            )
        with pytest.raises(ValueError, match="not registered"):
            ledger.deactivate(
                "missing",
                target=DeploymentAdmissionState.REVOKED,
                actor="operator",
                reason="missing",
            )
        ledger.deactivate(
            record.deployment_id,
            target=DeploymentAdmissionState.REVOKED,
            actor="operator",
            reason="revoke",
        )
        with pytest.raises(ValueError, match="only an active"):
            ledger.deactivate(
                record.deployment_id,
                target=DeploymentAdmissionState.REVOKED,
                actor="operator",
                reason="again",
            )
        with pytest.raises(ValueError, match="inactive"):
            ledger.require_active(admission, now=NOW + timedelta(minutes=2))
    finally:
        ledger.close()

    reader = DeploymentAdmissionLedger(path, read_only=True)
    try:
        with pytest.raises(ValueError, match="read-only"):
            reader.deactivate(
                admission.approval.deployment_id,
                target=DeploymentAdmissionState.REVOKED,
                actor="operator",
                reason="cannot write",
            )
    finally:
        reader.close()


def test_governance_cli_verifies_and_admits_through_explicit_controller_action(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admission, _paths = _signed_canary(tmp_path, config_dir)
    record = DeploymentAdmissionRecord(
        deployment_id=admission.approval.deployment_id,
        approval_id=admission.approval.approval_id,
        admission_id=admission.admission_id,
        authorization_id=admission.admission_id,
        renewal_count=0,
        approval_public_key_sha256=admission.public_key_sha256,
        stage=admission.approval.stage,
        account_address=admission.approval.account_address,
        artifact_manifest_sha256=admission.approval.artifact_manifest_sha256,
        configuration_sha256=admission.approval.configuration_sha256,
        image_digest=admission.approval.image_digest,
        capital_limit_usd=admission.approval.capital_limit_usd,
        admitted_at=NOW + timedelta(minutes=1),
        expires_at=admission.approval.expires_at,
        state=DeploymentAdmissionState.ACTIVE,
        actor="operator",
        reason="reviewed canary",
    )
    ledger = Mock()
    ledger.admit.return_value = record
    monkeypatch.setattr(
        "aiquanttrader.governance.cli.DeploymentAdmissionLedger",
        Mock(return_value=ledger),
    )
    monkeypatch.setattr(
        "aiquanttrader.governance.cli.configured_artifact_paths",
        Mock(return_value=object()),
    )
    verifier = Mock(return_value=admission)
    monkeypatch.setattr(
        "aiquanttrader.governance.cli.verify_deployment_admission",
        verifier,
    )
    identity = [
        "--config-dir",
        str(config_dir),
        "--environment",
        "canary",
        "--code-identity",
        COMMIT,
        "--image-identity",
        IMAGE,
        "--dependency-lock-path",
        str(tmp_path / "uv.lock"),
    ]
    assert governance_main(["verify", *identity]) == 0
    assert admission.admission_id in capsys.readouterr().out
    assert (
        governance_main(["admit", *identity, "--actor", "operator", "--reason", "reviewed canary"])
        == 0
    )
    assert record.deployment_id in capsys.readouterr().out
    ledger.admit.assert_called_once()
    ledger.close.assert_called_once()

    verifier.side_effect = ValueError("verification failed")
    assert governance_main(["verify", *identity]) == 2
    assert "verification failed" in capsys.readouterr().err
