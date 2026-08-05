"""Fail-closed Ed25519 verification of exact deployment admission bundles."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import (
    CANARY_CAPITAL_HARD_CAP_USD,
    PRODUCTION_CAPITAL_HARD_CAP_USD,
    DeploymentMode,
)
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader_native.governance.models import (
    DeploymentAdmissionRecord,
    DeploymentAdmissionState,
    DeploymentArtifactManifest,
    DeploymentAuthorizationRenewal,
    DetachedApprovalSignature,
    VerifiedDeploymentAdmission,
    VerifiedDeploymentRenewal,
)
from aiquanttrader_native.risk.authority import limits_sha

MAX_APPROVAL_BYTES = 262_144
MAX_PUBLIC_KEY_BYTES = 16_384
MAX_BOUND_ARTIFACT_BYTES = 134_217_728


class ApprovalVerificationError(ValueError):
    """The signed deployment bundle is missing, stale, forged, or mismatched."""


@dataclass(frozen=True, slots=True)
class ApprovalArtifactPaths:
    approval_path: Path
    manifest_path: Path
    signature_path: Path
    public_key_path: Path
    artifact_root: Path
    runtime_dependency_lock_path: Path


@dataclass(frozen=True, slots=True)
class RenewalApprovalPaths:
    renewal_path: Path
    signature_path: Path
    public_key_path: Path


def configured_artifact_paths(
    bundle: ConfigBundle, *, runtime_dependency_lock_path: Path
) -> ApprovalArtifactPaths:
    approval = bundle.settings.approval
    if not approval.complete_for(bundle.settings.mode):
        raise ApprovalVerificationError("deployment approval configuration is incomplete")
    if (
        approval.manifest_path is None
        or approval.approval_path is None
        or approval.signature_path is None
        or approval.public_key_path is None
        or approval.artifact_root_path is None
    ):
        raise ApprovalVerificationError("deployment approval paths are incomplete")
    return ApprovalArtifactPaths(
        approval_path=approval.approval_path,
        manifest_path=approval.manifest_path,
        signature_path=approval.signature_path,
        public_key_path=approval.public_key_path,
        artifact_root=approval.artifact_root_path,
        runtime_dependency_lock_path=runtime_dependency_lock_path,
    )


def verify_deployment_admission(
    bundle: ConfigBundle,
    paths: ApprovalArtifactPaths,
    *,
    code_identity: str,
    image_identity: str,
    now: datetime | None = None,
    wallet_role: Literal["trading", "control"] | None = None,
    wallet_address: str | None = None,
    require_active_approval: bool = True,
) -> VerifiedDeploymentAdmission:
    """Verify signature, artifact bytes, runtime identity, account, capital, and limits.

    Runtime processes may re-verify an expired original approval only when they
    immediately require its unchanged admission identity from the durable ledger.
    Controller verification and admission retain the active-approval requirement.
    """

    instant = datetime.now(UTC) if now is None else now
    if instant.tzinfo is None:
        raise ApprovalVerificationError("approval verification timestamp must be timezone-aware")
    if (wallet_role is None) != (wallet_address is None):
        raise ApprovalVerificationError("wallet role and address must be supplied together")

    approval = DeploymentApproval.model_validate_json(
        _read_regular(paths.approval_path, maximum_bytes=MAX_APPROVAL_BYTES)
    )
    manifest = DeploymentArtifactManifest.model_validate_json(
        _read_regular(paths.manifest_path, maximum_bytes=MAX_APPROVAL_BYTES)
    )
    signature = DetachedApprovalSignature.model_validate_json(
        _read_regular(paths.signature_path, maximum_bytes=MAX_APPROVAL_BYTES)
    )
    public_key_bytes = _read_regular(paths.public_key_path, maximum_bytes=MAX_PUBLIC_KEY_BYTES)
    public_key = _load_public_key(public_key_bytes)
    public_key_sha256 = hashlib.sha256(
        public_key.export_key(format="DER", compress=False)
    ).hexdigest()

    settings = bundle.settings
    configured = settings.approval
    manifest_sha256 = manifest.sha256()
    if configured.public_key_sha256 != public_key_sha256:
        raise ApprovalVerificationError("approval public key fingerprint does not match config")
    if configured.public_key_id != signature.key_id:
        raise ApprovalVerificationError("approval signature key identity does not match config")
    if signature.approval_sha256 != approval.sha256():
        raise ApprovalVerificationError("approval signature envelope binds different bytes")
    try:
        eddsa.new(public_key, "rfc8032").verify(
            approval.canonical_bytes(), signature.signature_bytes()
        )
    except ValueError as exc:
        raise ApprovalVerificationError("deployment approval Ed25519 signature is invalid") from exc

    if require_active_approval and not approval.is_active(instant):
        raise ApprovalVerificationError("deployment approval is not active")
    expected_stage = (
        PromotionStage.APPROVED_CANARY
        if settings.mode is DeploymentMode.CANARY
        else PromotionStage.PRODUCTION
        if settings.mode is DeploymentMode.PRODUCTION
        else None
    )
    if expected_stage is None or approval.stage is not expected_stage:
        raise ApprovalVerificationError("approval stage does not match deployment mode")
    if manifest.stage is not approval.stage:
        raise ApprovalVerificationError("artifact manifest stage does not match approval")
    if manifest.created_at > approval.approved_at:
        raise ApprovalVerificationError("approval predates its artifact manifest")

    expected_approval_id = configured.active_approval_id(settings.mode)
    expected_values: tuple[tuple[str, object, object], ...] = (
        ("approval_id", approval.approval_id, expected_approval_id),
        ("deployment_id", approval.deployment_id, configured.deployment_id),
        (
            "account_address",
            approval.account_address.lower(),
            _lower(settings.exchange.account_address),
        ),
        ("vault_address", _lower(approval.vault_address), _lower(settings.exchange.vault_address)),
        ("instrument_id", approval.instrument_id, settings.instrument.instrument_id),
        ("commit_sha", approval.commit_sha, code_identity),
        ("image_digest", approval.image_digest, image_identity),
        ("artifact_manifest_sha256", approval.artifact_manifest_sha256, manifest_sha256),
        (
            "configured_artifact_manifest_sha256",
            configured.artifact_manifest_sha256,
            manifest_sha256,
        ),
        (
            "configuration_sha256",
            approval.configuration_sha256,
            settings.approval_configuration_fingerprint(),
        ),
        ("risk_policy_sha256", approval.risk_policy_sha256, limits_sha(settings.risk)),
        (
            "rollback_deployment_id",
            approval.rollback_deployment_id,
            manifest.rollback_deployment_id,
        ),
    )
    for field, actual, expected in expected_values:
        if actual != expected:
            raise ApprovalVerificationError(f"deployment approval mismatch: {field}")
    _assert_manifest_matches_approval(manifest, approval)

    runtime_lock_sha256 = _sha256_regular(
        paths.runtime_dependency_lock_path, maximum_bytes=MAX_BOUND_ARTIFACT_BYTES
    )
    if runtime_lock_sha256 != approval.dependency_lock_sha256:
        raise ApprovalVerificationError("runtime dependency lock differs from approved lock")
    _verify_bound_artifacts(paths.artifact_root, manifest)

    capital_hard_cap = (
        CANARY_CAPITAL_HARD_CAP_USD
        if settings.mode is DeploymentMode.CANARY
        else PRODUCTION_CAPITAL_HARD_CAP_USD
    )
    if approval.capital_limit_usd > capital_hard_cap:
        raise ApprovalVerificationError("approval capital exceeds the immutable mode hard cap")
    if settings.risk.max_inventory_notional_usd > approval.capital_limit_usd:
        raise ApprovalVerificationError("inventory limit exceeds approved capital")
    if (
        settings.risk.max_inventory_notional_usd
        > approval.capital_limit_usd * settings.risk.max_leverage
    ):
        raise ApprovalVerificationError("inventory limit exceeds approved leveraged capital")

    if wallet_role is not None and wallet_address is not None:
        expected_wallet = (
            approval.trading_wallet_address
            if wallet_role == "trading"
            else approval.control_wallet_address
        )
        if wallet_address.lower() != expected_wallet.lower():
            raise ApprovalVerificationError(f"{wallet_role} wallet does not match approval")

    payload = {
        "schema_version": 1,
        "approval": approval.model_dump(mode="json"),
        "artifact_manifest": manifest.model_dump(mode="json"),
        "public_key_sha256": public_key_sha256,
        "signature_envelope_sha256": signature.sha256(),
    }
    return VerifiedDeploymentAdmission.model_validate(
        {
            "admission_id": canonical_sha256(payload),
            "verified_at": instant.isoformat(),
            **payload,
        }
    )


def verify_deployment_renewal(
    *,
    paths: RenewalApprovalPaths,
    current: DeploymentAdmissionRecord,
    expected_key_id: str,
    expected_public_key_sha256: str,
    now: datetime | None = None,
) -> VerifiedDeploymentRenewal:
    """Verify a short-lived, chained renewal for one unchanged production admission."""

    instant = datetime.now(UTC) if now is None else now
    if instant.tzinfo is None:
        raise ApprovalVerificationError("renewal verification timestamp must be timezone-aware")
    if current.state is not DeploymentAdmissionState.ACTIVE:
        raise ApprovalVerificationError("deployment renewal requires an active admission")
    if current.stage is not PromotionStage.PRODUCTION:
        raise ApprovalVerificationError("only production admissions may be renewed")
    if instant >= current.expires_at:
        raise ApprovalVerificationError("expired deployment admission cannot be renewed")
    if current.approval_public_key_sha256 is None:
        raise ApprovalVerificationError("deployment admission has no renewal trust root")
    if expected_public_key_sha256 != current.approval_public_key_sha256:
        raise ApprovalVerificationError("renewal trust root differs from admitted release")

    renewal = DeploymentAuthorizationRenewal.model_validate_json(
        _read_regular(paths.renewal_path, maximum_bytes=MAX_APPROVAL_BYTES)
    )
    signature = DetachedApprovalSignature.model_validate_json(
        _read_regular(paths.signature_path, maximum_bytes=MAX_APPROVAL_BYTES)
    )
    public_key = _load_public_key(
        _read_regular(paths.public_key_path, maximum_bytes=MAX_PUBLIC_KEY_BYTES)
    )
    public_key_sha256 = hashlib.sha256(
        public_key.export_key(format="DER", compress=False)
    ).hexdigest()
    if public_key_sha256 != expected_public_key_sha256:
        raise ApprovalVerificationError("renewal public key fingerprint does not match config")
    if signature.key_id != expected_key_id:
        raise ApprovalVerificationError("renewal signature key identity does not match config")
    if signature.approval_sha256 != renewal.sha256():
        raise ApprovalVerificationError("renewal signature envelope binds different bytes")
    try:
        eddsa.new(public_key, "rfc8032").verify(
            renewal.canonical_bytes(), signature.signature_bytes()
        )
    except ValueError as exc:
        raise ApprovalVerificationError("deployment renewal Ed25519 signature is invalid") from exc
    if not renewal.is_active(instant):
        raise ApprovalVerificationError("deployment renewal is not active")

    expected_values: tuple[tuple[str, object, object], ...] = (
        ("deployment_id", renewal.deployment_id, current.deployment_id),
        ("initial_approval_id", renewal.initial_approval_id, current.approval_id),
        ("admission_id", renewal.admission_id, current.admission_id),
        (
            "prior_authorization_id",
            renewal.prior_authorization_id,
            current.authorization_id,
        ),
        ("stage", renewal.stage, current.stage),
        ("account_address", renewal.account_address.lower(), current.account_address.lower()),
        ("vault_address", _lower(renewal.vault_address), _lower(current.vault_address)),
        (
            "artifact_manifest_sha256",
            renewal.artifact_manifest_sha256,
            current.artifact_manifest_sha256,
        ),
        ("configuration_sha256", renewal.configuration_sha256, current.configuration_sha256),
        ("image_digest", renewal.image_digest, current.image_digest),
        ("capital_limit_usd", renewal.capital_limit_usd, current.capital_limit_usd),
    )
    for field, actual, expected in expected_values:
        if actual != expected:
            raise ApprovalVerificationError(f"deployment renewal mismatch: {field}")
    if renewal.expires_at <= current.expires_at:
        raise ApprovalVerificationError("deployment renewal must extend the authorization window")
    if renewal.approved_at < current.admitted_at:
        raise ApprovalVerificationError("deployment renewal predates the admission")

    payload = {
        "schema_version": 1,
        "renewal": renewal.model_dump(mode="json"),
        "public_key_sha256": public_key_sha256,
        "signature_envelope_sha256": signature.sha256(),
    }
    return VerifiedDeploymentRenewal.model_validate(
        {
            **payload,
            "authorization_id": canonical_sha256(payload),
            "verified_at": instant.isoformat(),
        }
    )


def _assert_manifest_matches_approval(
    manifest: DeploymentArtifactManifest, approval: DeploymentApproval
) -> None:
    fields = (
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
    for field in fields:
        if getattr(manifest, field) != getattr(approval, field):
            raise ApprovalVerificationError(f"artifact manifest mismatch: {field}")


def _verify_bound_artifacts(root: Path, manifest: DeploymentArtifactManifest) -> None:
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ApprovalVerificationError("deployment artifact root is not a directory")
    for binding in manifest.artifacts:
        path = (resolved_root / binding.relative_path).resolve(strict=True)
        if not path.is_relative_to(resolved_root):
            raise ApprovalVerificationError("deployment artifact escapes its approved root")
        observed = _sha256_regular(path, maximum_bytes=MAX_BOUND_ARTIFACT_BYTES)
        if observed != binding.content_sha256:
            raise ApprovalVerificationError(
                f"deployment artifact content mismatch: {binding.kind.value}"
            )


def _load_public_key(payload: bytes) -> ECC.EccKey:
    try:
        key = ECC.import_key(payload)
    except (ValueError, IndexError, TypeError) as exc:
        raise ApprovalVerificationError("approval public key cannot be parsed") from exc
    if key.has_private() or key.curve != "Ed25519":
        raise ApprovalVerificationError("approval trust root must be an Ed25519 public key")
    return key


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ApprovalVerificationError(f"cannot open deployment artifact: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ApprovalVerificationError(f"deployment artifact is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ApprovalVerificationError(f"deployment artifact size is invalid: {path.name}")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size:
            raise ApprovalVerificationError(f"deployment artifact changed while read: {path.name}")
        return payload
    finally:
        os.close(descriptor)


def _sha256_regular(path: Path, *, maximum_bytes: int) -> str:
    return hashlib.sha256(_read_regular(path, maximum_bytes=maximum_bytes)).hexdigest()


def _lower(value: str | None) -> str | None:
    return None if value is None else value.lower()
