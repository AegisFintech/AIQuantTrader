from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.models import (
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
)


def test_retirement_cli_canonicalizes_and_verifies_but_has_no_action_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime.now(UTC)
    approval = RetirementActionApproval(
        approval_id="retirement-stop-cli-001",
        retirement_id="retirement-cli-001",
        scope=RetirementActionScope.STOP_AND_OBSERVE,
        report_sha256="1" * 64,
        native_deployment_id="native-production-cli-001",
        native_admission_id="2" * 64,
        archive_manifest_sha256="3" * 64,
        source_commit_sha="4" * 40,
        approver="risk-owner",
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    pretty = tmp_path / "pretty.json"
    canonical = tmp_path / "approval.json"
    signature_path = tmp_path / "approval.sig.json"
    public_key_path = tmp_path / "approver.pub"
    pretty.write_text(approval.model_dump_json(indent=2), encoding="utf-8")

    assert (
        retirement_main(
            [
                "canonicalize-approval",
                "--input",
                str(pretty),
                "--output",
                str(canonical),
            ]
        )
        == 0
    )
    assert canonical.read_bytes() == approval.canonical_bytes()
    assert (
        retirement_main(
            [
                "canonicalize-approval",
                "--input",
                str(pretty),
                "--output",
                str(canonical),
            ]
        )
        == 2
    )
    assert canonical.read_bytes() == approval.canonical_bytes()

    key = ECC.generate(curve="Ed25519")
    signature = RetirementApprovalSignature(
        key_id="retirement-approver-cli",
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(
            eddsa.new(key, "rfc8032").sign(approval.canonical_bytes())
        ).decode("ascii"),
    )
    signature_path.write_bytes(signature.canonical_bytes())
    public_key_path.write_text(key.public_key().export_key(format="PEM"), encoding="ascii")
    fingerprint = hashlib.sha256(
        key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()

    assert (
        retirement_main(
            [
                "verify-approval",
                "--approval",
                str(canonical),
                "--signature",
                str(signature_path),
                "--public-key",
                str(public_key_path),
                "--public-key-sha256",
                fingerprint,
                "--key-id",
                signature.key_id,
                "--expected-retirement-id",
                approval.retirement_id,
                "--expected-scope",
                approval.scope.value,
                "--expected-report-sha256",
                approval.report_sha256,
                "--expected-native-deployment-id",
                approval.native_deployment_id,
                "--expected-native-admission-id",
                approval.native_admission_id,
                "--expected-archive-manifest-sha256",
                approval.archive_manifest_sha256,
                "--expected-source-commit-sha",
                approval.source_commit_sha,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert approval.approval_id in captured.out

    with pytest.raises(SystemExit):
        retirement_main(["stop"])
