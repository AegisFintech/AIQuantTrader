"""Offline Ed25519 verification for one exact Phase 10 operator action."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.retirement.models import (
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
    VerifiedRetirementApproval,
)

MAX_RETIREMENT_APPROVAL_BYTES = 262_144
MAX_RETIREMENT_PUBLIC_KEY_BYTES = 16_384


class RetirementApprovalError(ValueError):
    """The retirement approval is missing, expired, forged, or mismatched."""


@dataclass(frozen=True, slots=True)
class RetirementApprovalPaths:
    approval_path: Path
    signature_path: Path
    public_key_path: Path


@dataclass(frozen=True, slots=True)
class ExpectedRetirementAction:
    retirement_id: str
    scope: RetirementActionScope
    report_sha256: str
    native_deployment_id: str
    native_admission_id: str
    archive_manifest_sha256: str
    source_commit_sha: str
    cleanup_manifest_sha256: str | None = None


def verify_retirement_approval(
    *,
    paths: RetirementApprovalPaths,
    expected: ExpectedRetirementAction,
    expected_key_id: str,
    expected_public_key_sha256: str,
    now: datetime | None = None,
) -> VerifiedRetirementApproval:
    """Verify the signature, active window, trust root, scope, and exact evidence binding."""

    instant = datetime.now(UTC) if now is None else now
    if instant.tzinfo is None:
        raise RetirementApprovalError("retirement verification timestamp must be timezone-aware")
    approval = RetirementActionApproval.model_validate_json(
        _read_regular(paths.approval_path, maximum_bytes=MAX_RETIREMENT_APPROVAL_BYTES)
    )
    signature = RetirementApprovalSignature.model_validate_json(
        _read_regular(paths.signature_path, maximum_bytes=MAX_RETIREMENT_APPROVAL_BYTES)
    )
    public_key_bytes = _read_regular(
        paths.public_key_path, maximum_bytes=MAX_RETIREMENT_PUBLIC_KEY_BYTES
    )
    public_key = _load_public_key(public_key_bytes)
    public_key_sha256 = hashlib.sha256(
        public_key.export_key(format="DER", compress=False)
    ).hexdigest()
    if public_key_sha256 != expected_public_key_sha256:
        raise RetirementApprovalError("retirement public key fingerprint does not match")
    if signature.key_id != expected_key_id:
        raise RetirementApprovalError("retirement signature key identity does not match")
    if signature.approval_sha256 != approval.sha256():
        raise RetirementApprovalError("retirement signature binds different approval bytes")
    try:
        eddsa.new(public_key, "rfc8032").verify(
            approval.canonical_bytes(), signature.signature_bytes()
        )
    except ValueError as exc:
        raise RetirementApprovalError("retirement Ed25519 signature is invalid") from exc
    if not approval.is_active(instant):
        raise RetirementApprovalError("retirement approval is not active")

    expected_values: tuple[tuple[str, object, object], ...] = (
        ("retirement_id", approval.retirement_id, expected.retirement_id),
        ("scope", approval.scope, expected.scope),
        ("report_sha256", approval.report_sha256, expected.report_sha256),
        ("native_deployment_id", approval.native_deployment_id, expected.native_deployment_id),
        ("native_admission_id", approval.native_admission_id, expected.native_admission_id),
        (
            "archive_manifest_sha256",
            approval.archive_manifest_sha256,
            expected.archive_manifest_sha256,
        ),
        ("source_commit_sha", approval.source_commit_sha, expected.source_commit_sha),
        (
            "cleanup_manifest_sha256",
            approval.cleanup_manifest_sha256,
            expected.cleanup_manifest_sha256,
        ),
    )
    for name, actual, wanted in expected_values:
        if actual != wanted:
            raise RetirementApprovalError(f"retirement approval mismatch: {name}")

    payload = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "public_key_sha256": public_key_sha256,
        "signature_envelope_sha256": signature.sha256(),
    }
    return VerifiedRetirementApproval.model_validate(
        {
            **payload,
            "verification_id": canonical_sha256(payload),
            "verified_at": instant.isoformat(),
        }
    )


def _load_public_key(payload: bytes) -> ECC.EccKey:
    try:
        key = ECC.import_key(payload)
    except (ValueError, IndexError, TypeError) as exc:
        raise RetirementApprovalError("retirement public key cannot be parsed") from exc
    if key.has_private() or key.curve != "Ed25519":
        raise RetirementApprovalError("retirement trust root must be an Ed25519 public key")
    return key


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RetirementApprovalError(f"cannot open retirement artifact: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RetirementApprovalError(f"retirement artifact is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise RetirementApprovalError(f"retirement artifact size is invalid: {path.name}")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        if len(payload) != metadata.st_size:
            raise RetirementApprovalError(f"retirement artifact changed while read: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)
