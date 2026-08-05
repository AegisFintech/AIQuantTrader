from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from aiquanttrader.retirement.approval import (
    ExpectedRetirementAction,
    RetirementApprovalError,
    RetirementApprovalPaths,
    verify_retirement_approval,
)
from aiquanttrader.retirement.models import (
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
)

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
COMMIT = "b" * 40


def _signed_approval(
    tmp_path: Path,
) -> tuple[RetirementActionApproval, ExpectedRetirementAction, RetirementApprovalPaths, str]:
    approval = RetirementActionApproval(
        approval_id="retirement-stop-001",
        retirement_id="retirement-001",
        scope=RetirementActionScope.STOP_AND_OBSERVE,
        report_sha256="1" * 64,
        native_deployment_id="native-production-001",
        native_admission_id="2" * 64,
        archive_manifest_sha256="3" * 64,
        source_commit_sha=COMMIT,
        approver="risk-owner",
        approved_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    key = ECC.generate(curve="Ed25519")
    signature = eddsa.new(key, "rfc8032").sign(approval.canonical_bytes())
    envelope = RetirementApprovalSignature(
        key_id="retirement-approver-001",
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(signature).decode("ascii"),
    )
    approval_path = tmp_path / "approval.json"
    signature_path = tmp_path / "approval.sig.json"
    public_path = tmp_path / "approver.pub"
    approval_path.write_bytes(approval.canonical_bytes())
    signature_path.write_bytes(envelope.canonical_bytes())
    public_path.write_text(key.public_key().export_key(format="PEM"), encoding="ascii")
    fingerprint = hashlib.sha256(
        key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()
    expected = ExpectedRetirementAction(
        retirement_id=approval.retirement_id,
        scope=approval.scope,
        report_sha256=approval.report_sha256,
        native_deployment_id=approval.native_deployment_id,
        native_admission_id=approval.native_admission_id,
        archive_manifest_sha256=approval.archive_manifest_sha256,
        source_commit_sha=approval.source_commit_sha,
    )
    return (
        approval,
        expected,
        RetirementApprovalPaths(approval_path, signature_path, public_path),
        fingerprint,
    )


def test_retirement_approval_verifies_exact_action_and_detects_mismatch(tmp_path: Path) -> None:
    approval, expected, paths, fingerprint = _signed_approval(tmp_path)
    verified = verify_retirement_approval(
        paths=paths,
        expected=expected,
        expected_key_id="retirement-approver-001",
        expected_public_key_sha256=fingerprint,
        now=NOW,
    )

    assert verified.approval == approval
    assert verified.public_key_sha256 == fingerprint
    with pytest.raises(RetirementApprovalError, match="report_sha256"):
        verify_retirement_approval(
            paths=paths,
            expected=replace(expected, report_sha256="f" * 64),
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )
    with pytest.raises(RetirementApprovalError, match="not active"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW + timedelta(days=1),
        )


def test_retirement_approval_rejects_tampering_and_wrong_trust_root(tmp_path: Path) -> None:
    _approval, expected, paths, fingerprint = _signed_approval(tmp_path)
    with pytest.raises(RetirementApprovalError, match="fingerprint"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256="0" * 64,
            now=NOW,
        )

    payload = bytearray(paths.approval_path.read_bytes())
    payload[payload.index(b"risk-owner")] = ord("R")
    paths.approval_path.write_bytes(payload)
    with pytest.raises(RetirementApprovalError, match="binds different"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )


def test_retirement_approval_rejects_bad_key_signature_identity_and_files(tmp_path: Path) -> None:
    _approval, expected, paths, fingerprint = _signed_approval(tmp_path)
    with pytest.raises(RetirementApprovalError, match="timezone-aware"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=datetime(2026, 8, 5, 12),
        )
    with pytest.raises(RetirementApprovalError, match="key identity"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="wrong-key",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )

    envelope = RetirementApprovalSignature.model_validate_json(paths.signature_path.read_bytes())
    invalid_signature = envelope.model_copy(
        update={"signature_base64": base64.b64encode(b"x" * 64).decode("ascii")}
    )
    paths.signature_path.write_bytes(invalid_signature.canonical_bytes())
    with pytest.raises(RetirementApprovalError, match="signature is invalid"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id=envelope.key_id,
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _approval, expected, paths, fingerprint = _signed_approval(fresh)
    paths.public_key_path.write_bytes(b"not a public key")
    with pytest.raises(RetirementApprovalError, match="cannot be parsed"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )

    paths.public_key_path.unlink()
    with pytest.raises(RetirementApprovalError, match="cannot open"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )

    paths.public_key_path.write_bytes(b"")
    with pytest.raises(RetirementApprovalError, match="size is invalid"):
        verify_retirement_approval(
            paths=paths,
            expected=expected,
            expected_key_id="retirement-approver-001",
            expected_public_key_sha256=fingerprint,
            now=NOW,
        )
