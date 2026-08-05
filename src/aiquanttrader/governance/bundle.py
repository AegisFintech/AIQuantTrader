"""Deterministic, credential-free preparation of unsigned release bundles."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tomllib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.config.models import (
    CANARY_CAPITAL_HARD_CAP_USD,
    PRODUCTION_CAPITAL_HARD_CAP_USD,
    DeploymentMode,
    NativeSettings,
)
from aiquanttrader.domain.base import canonical_json_bytes, canonical_sha256
from aiquanttrader.domain.data import DatasetManifest
from aiquanttrader.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader.features.models import FeatureEngineConfig, FeatureSchema
from aiquanttrader.governance.models import (
    CanaryEvidenceReport,
    DeploymentArtifactBinding,
    DeploymentArtifactKind,
    DeploymentArtifactManifest,
    DeploymentModelSelection,
    ReleaseBundleReceipt,
    ReleaseBundleSpec,
    ReleaseFileDigest,
    TestnetDressRehearsalReport,
)
from aiquanttrader.risk.authority import limits_sha
from aiquanttrader.shadow.models import ShadowEvidenceReport
from aiquanttrader.strategies.market_maker import AvellanedaStoikovConfig
from aiquanttrader.strategies.scalper import OrderFlowScalperConfig

MAX_RELEASE_ARTIFACT_BYTES = 134_217_728

ARTIFACT_DESTINATIONS: dict[DeploymentArtifactKind, str] = {
    DeploymentArtifactKind.DEPENDENCY_LOCK: "uv.lock",
    DeploymentArtifactKind.DATASET_MANIFEST: "dataset-manifest.json",
    DeploymentArtifactKind.MODEL_MANIFEST: "model-manifest.json",
    DeploymentArtifactKind.FEATURE_SCHEMA: "feature-schema.json",
    DeploymentArtifactKind.STRATEGY_CONFIG: "strategy-config.toml",
    DeploymentArtifactKind.RISK_POLICY: "risk-policy.json",
    DeploymentArtifactKind.SHADOW_EVIDENCE: "shadow-evidence.json",
    DeploymentArtifactKind.TESTNET_EVIDENCE: "testnet-evidence.json",
    DeploymentArtifactKind.CANARY_EVIDENCE: "canary-evidence.json",
}

StrategyConfiguration = AvellanedaStoikovConfig | OrderFlowScalperConfig
STRATEGY_ADAPTER: TypeAdapter[StrategyConfiguration] = TypeAdapter(StrategyConfiguration)

SHADOW_REQUIRED_GATES = frozenset(
    {
        "observation",
        "independent_decisions",
        "fills",
        "regimes",
        "availability",
        "ingress_latency_p99_ms",
        "cycle_latency_p99_ms",
        "operational_sample_completeness",
        "command_completeness",
        "calibrated_scenario",
        "positive_post_cost_pnl",
        "drawdown",
        "denial_fraction",
        "markout_coverage",
        "adverse_markout",
        "drift_evaluated",
        "feature_psi",
        "feature_mean_shift",
        "determinism",
        "sensitivity",
        "drills",
        "run_integrity",
        "flat_final_state",
    }
)
CANARY_REQUIRED_GATES = frozenset(
    {
        "observation",
        "orders",
        "fills",
        "maker_fills",
        "rejection_fraction",
        "unknown_outcomes",
        "reconciliation_failures",
        "fee_attribution",
        "funding_attribution",
        "positive_post_cost_pnl",
        "drawdown",
        "adverse_markout",
        "capital",
        "drills",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    kind: DeploymentArtifactKind
    payload: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def relative_path(self) -> str:
        return f"artifacts/{ARTIFACT_DESTINATIONS[self.kind]}"


def load_release_bundle_spec(path: Path) -> ReleaseBundleSpec:
    payload = _read_regular(path, maximum_bytes=1_048_576)
    try:
        parsed = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("release bundle specification is not valid UTF-8 TOML") from exc
    return ReleaseBundleSpec.model_validate(parsed)


def prepare_release_bundle(
    *,
    bundle: ConfigBundle,
    spec: ReleaseBundleSpec,
    output_dir: Path,
) -> ReleaseBundleReceipt:
    """Validate evidence and atomically write an unsigned, non-authoritative bundle."""

    if not output_dir.is_absolute():
        raise ValueError("release bundle output directory must be absolute")
    output_dir = output_dir.resolve(strict=False)
    if output_dir.exists():
        raise ValueError("release bundle output directory already exists")
    parent = output_dir.parent.resolve(strict=True)
    settings, behavior_payload, behavior_sha256 = release_behavior_configuration(bundle, spec)

    feature_config_payload = _live_feature_config_payload(bundle, settings)
    artifacts = _load_and_validate_artifacts(
        spec,
        settings,
        behavior_sha256,
        feature_config_payload=feature_config_payload,
    )
    by_kind = {artifact.kind: artifact for artifact in artifacts}
    manifest = DeploymentArtifactManifest(
        deployment_id=spec.deployment_id,
        stage=spec.stage,
        created_at=spec.approved_at,
        commit_sha=spec.commit_sha,
        image_digest=spec.image_digest,
        configuration_sha256=behavior_sha256,
        dependency_lock_sha256=by_kind[DeploymentArtifactKind.DEPENDENCY_LOCK].sha256,
        dataset_sha256=by_kind[DeploymentArtifactKind.DATASET_MANIFEST].sha256,
        model_sha256=by_kind[DeploymentArtifactKind.MODEL_MANIFEST].sha256,
        feature_schema_sha256=by_kind[DeploymentArtifactKind.FEATURE_SCHEMA].sha256,
        strategy_config_sha256=by_kind[DeploymentArtifactKind.STRATEGY_CONFIG].sha256,
        risk_policy_sha256=by_kind[DeploymentArtifactKind.RISK_POLICY].sha256,
        shadow_evidence_sha256=by_kind[DeploymentArtifactKind.SHADOW_EVIDENCE].sha256,
        testnet_evidence_sha256=by_kind[DeploymentArtifactKind.TESTNET_EVIDENCE].sha256,
        canary_evidence_sha256=(
            by_kind[DeploymentArtifactKind.CANARY_EVIDENCE].sha256
            if DeploymentArtifactKind.CANARY_EVIDENCE in by_kind
            else None
        ),
        rollback_deployment_id=spec.rollback_deployment_id,
        artifacts=tuple(
            DeploymentArtifactBinding(
                kind=artifact.kind,
                relative_path=artifact.relative_path.removeprefix("artifacts/"),
                content_sha256=artifact.sha256,
            )
            for artifact in artifacts
        ),
    )
    approval = DeploymentApproval(
        approval_id=spec.approval_id,
        deployment_id=spec.deployment_id,
        stage=spec.stage,
        account_address=spec.account_address,
        vault_address=spec.vault_address,
        trading_wallet_address=spec.trading_wallet_address,
        control_wallet_address=spec.control_wallet_address,
        commit_sha=spec.commit_sha,
        image_digest=spec.image_digest,
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
        capital_limit_usd=spec.capital_limit_usd,
        rollback_deployment_id=spec.rollback_deployment_id,
        prior_approval_id=spec.prior_approval_id,
        approver=spec.approver,
        approved_at=spec.approved_at,
        expires_at=spec.expires_at,
    )
    manifest_payload = manifest.canonical_bytes()
    approval_payload = approval.canonical_bytes()
    files = [
        *(
            ReleaseFileDigest(
                relative_path=artifact.relative_path,
                content_sha256=artifact.sha256,
                byte_count=len(artifact.payload),
            )
            for artifact in artifacts
        ),
        _digest("artifact-manifest.json", manifest_payload),
        _digest("deployment-approval.unsigned.json", approval_payload),
        _digest("behavior-configuration.json", behavior_payload),
    ]
    receipt_identity: dict[str, Any] = {
        "schema_version": 1,
        "deployment_id": spec.deployment_id,
        "stage": spec.stage,
        "artifact_manifest_sha256": manifest.sha256(),
        "unsigned_approval_sha256": approval.sha256(),
        "behavior_configuration_sha256": behavior_sha256,
        "files": [item.model_dump(mode="json") for item in files],
        "awaiting_offline_signature": True,
    }
    receipt = ReleaseBundleReceipt.model_validate(
        {"receipt_id": canonical_sha256(receipt_identity), **receipt_identity}
    )
    _write_bundle(
        parent=parent,
        output_dir=output_dir,
        artifacts=artifacts,
        manifest_payload=manifest_payload,
        approval_payload=approval_payload,
        behavior_payload=behavior_payload,
        receipt_payload=receipt.canonical_bytes(),
    )
    return receipt


def release_behavior_configuration(
    bundle: ConfigBundle,
    spec: ReleaseBundleSpec,
) -> tuple[NativeSettings, bytes, str]:
    """Render the exact non-approval behavior which rehearsal and signing bind."""

    settings = _release_settings(bundle, spec)
    payload = canonical_json_bytes(settings.model_dump(mode="json", exclude={"approval"}))
    sha256 = hashlib.sha256(payload).hexdigest()
    if sha256 != settings.approval_configuration_fingerprint():
        raise ValueError("release behavior configuration identity is inconsistent")
    return settings, payload, sha256


def _release_settings(bundle: ConfigBundle, spec: ReleaseBundleSpec) -> NativeSettings:
    expected_mode = (
        DeploymentMode.CANARY
        if spec.stage is PromotionStage.APPROVED_CANARY
        else DeploymentMode.PRODUCTION
    )
    if bundle.settings.mode is not expected_mode:
        raise ValueError("release specification stage does not match configuration environment")
    strategy = _strategy_configuration(
        _read_regular(spec.artifacts.strategy_config, maximum_bytes=MAX_RELEASE_ARTIFACT_BYTES)
    )
    payload = bundle.settings.model_dump(mode="json")
    payload["exchange"].update(
        {
            "account_address": spec.account_address,
            "vault_address": spec.vault_address,
            "trading_wallet_secret_path": "/run/secrets/mainnet-trading-wallet",
            "control_wallet_secret_path": "/run/secrets/mainnet-control-wallet",
        }
    )
    payload["execution"]["enabled"] = True
    payload["live_strategy"].update(
        {
            "enabled": True,
            "strategy_id": strategy.strategy_id,
        }
    )
    payload["sentinel"]["enabled"] = True
    payload["risk"] = spec.risk.model_dump(mode="json")
    payload["approval"] = {
        "deployment_id": spec.deployment_id,
        "approval_id": (
            spec.approval_id if expected_mode is DeploymentMode.CANARY else spec.prior_approval_id
        ),
        "scale_approval_id": (
            spec.approval_id if expected_mode is DeploymentMode.PRODUCTION else None
        ),
        "artifact_manifest_sha256": "0" * 64,
        "approval_path": "/run/approvals/deployment-approval.json",
        "manifest_path": "/run/approvals/artifact-manifest.json",
        "signature_path": "/run/approvals/deployment-approval.sig.json",
        "public_key_path": "/run/approvals/approver-ed25519.pub.pem",
        "public_key_id": "offline-release-approver",
        "public_key_sha256": "0" * 64,
        "artifact_root_path": "/run/approvals/artifacts",
    }
    return NativeSettings.model_validate(payload)


def _load_and_validate_artifacts(
    spec: ReleaseBundleSpec,
    settings: NativeSettings,
    behavior_sha256: str,
    *,
    feature_config_payload: bytes,
) -> tuple[PreparedArtifact, ...]:
    sources = spec.artifacts
    source_by_kind: dict[DeploymentArtifactKind, Path] = {
        DeploymentArtifactKind.DEPENDENCY_LOCK: sources.dependency_lock,
        DeploymentArtifactKind.DATASET_MANIFEST: sources.dataset_manifest,
        DeploymentArtifactKind.MODEL_MANIFEST: sources.model_manifest,
        DeploymentArtifactKind.FEATURE_SCHEMA: sources.feature_schema,
        DeploymentArtifactKind.STRATEGY_CONFIG: sources.strategy_config,
        DeploymentArtifactKind.SHADOW_EVIDENCE: sources.shadow_evidence,
        DeploymentArtifactKind.TESTNET_EVIDENCE: sources.testnet_evidence,
    }
    if sources.canary_evidence is not None:
        source_by_kind[DeploymentArtifactKind.CANARY_EVIDENCE] = sources.canary_evidence
    payloads = {
        kind: _read_regular(path, maximum_bytes=MAX_RELEASE_ARTIFACT_BYTES)
        for kind, path in source_by_kind.items()
    }
    payloads[DeploymentArtifactKind.RISK_POLICY] = canonical_json_bytes(
        settings.risk.model_dump(mode="json")
    )
    _validate_semantics(
        payloads,
        spec,
        settings,
        behavior_sha256,
        feature_config_payload=feature_config_payload,
    )
    return tuple(
        PreparedArtifact(kind=kind, payload=payloads[kind])
        for kind in DeploymentArtifactKind
        if kind in payloads
    )


def _validate_semantics(
    payloads: dict[DeploymentArtifactKind, bytes],
    spec: ReleaseBundleSpec,
    settings: NativeSettings,
    behavior_sha256: str,
    *,
    feature_config_payload: bytes,
) -> None:
    DatasetManifest.model_validate_json(payloads[DeploymentArtifactKind.DATASET_MANIFEST])
    feature_schema = FeatureSchema.model_validate_json(
        payloads[DeploymentArtifactKind.FEATURE_SCHEMA]
    )
    model_selection = DeploymentModelSelection.model_validate_json(
        payloads[DeploymentArtifactKind.MODEL_MANIFEST]
    )
    strategy = _strategy_configuration(payloads[DeploymentArtifactKind.STRATEGY_CONFIG])
    FeatureEngineConfig.model_validate(
        _toml_configuration(feature_config_payload, label="live feature")
    )
    if strategy.strategy_id != settings.live_strategy.strategy_id:
        raise ValueError("release strategy identity does not match target live behavior")
    if strategy.strategy_id != model_selection.strategy_id:
        raise ValueError("release strategy and model selection identities do not match")
    if strategy.order_quantity_base > settings.risk.max_order_size_base:
        raise ValueError("release strategy order quantity exceeds the hard order-size limit")
    if strategy.max_abs_inventory_base > settings.risk.max_position_size_base:
        raise ValueError("release strategy inventory bound exceeds the hard position limit")
    if isinstance(strategy, AvellanedaStoikovConfig) and settings.risk.max_open_orders < 2:
        raise ValueError("release market maker requires one bid and one ask order slot")
    if feature_schema.sha256() != model_selection.feature_schema_sha256:
        raise ValueError("release feature schema and model selection do not match")
    dependency_sha = _sha(payloads[DeploymentArtifactKind.DEPENDENCY_LOCK])
    if (
        model_selection.model is not None
        and model_selection.model.dependency_lock_sha256 != dependency_sha
    ):
        raise ValueError("selected model was built with a different dependency lock")

    digests = {kind: _sha(payload) for kind, payload in payloads.items()}
    if digests[DeploymentArtifactKind.RISK_POLICY] != limits_sha(settings.risk):
        raise ValueError("generated release risk policy identity is inconsistent")
    shadow = ShadowEvidenceReport.model_validate_json(
        payloads[DeploymentArtifactKind.SHADOW_EVIDENCE]
    )
    _require_complete_gates(
        (gate.gate for gate in shadow.gates),
        expected=SHADOW_REQUIRED_GATES,
        label="shadow evidence",
    )
    if not shadow.awaiting_human_approval or not all(gate.passed for gate in shadow.gates):
        raise ValueError("shadow evidence has not reached the human approval boundary")
    if (
        shadow.code_identity != spec.commit_sha
        or shadow.image_identity != spec.image_digest
        or shadow.feature_config_sha256 != _sha(feature_config_payload)
        or shadow.strategy_config_sha256 != digests[DeploymentArtifactKind.STRATEGY_CONFIG]
    ):
        raise ValueError("shadow evidence does not bind the proposed release")

    testnet = TestnetDressRehearsalReport.model_validate_json(
        payloads[DeploymentArtifactKind.TESTNET_EVIDENCE]
    )
    expected_testnet = {
        "commit_sha": spec.commit_sha,
        "image_digest": spec.image_digest,
        "dependency_lock_sha256": digests[DeploymentArtifactKind.DEPENDENCY_LOCK],
        "dataset_sha256": digests[DeploymentArtifactKind.DATASET_MANIFEST],
        "model_sha256": digests[DeploymentArtifactKind.MODEL_MANIFEST],
        "feature_schema_sha256": digests[DeploymentArtifactKind.FEATURE_SCHEMA],
        "strategy_config_sha256": digests[DeploymentArtifactKind.STRATEGY_CONFIG],
        "risk_policy_sha256": digests[DeploymentArtifactKind.RISK_POLICY],
        "target_configuration_sha256": behavior_sha256,
    }
    if not testnet.awaiting_canary_approval or not all(gate.passed for gate in testnet.gates):
        raise ValueError("testnet evidence has not reached the canary approval boundary")
    for field, expected in expected_testnet.items():
        actual = getattr(testnet, field)
        if actual != expected:
            raise ValueError(f"testnet evidence mismatch: {field}")

    capital_hard_cap = (
        CANARY_CAPITAL_HARD_CAP_USD
        if spec.stage is PromotionStage.APPROVED_CANARY
        else PRODUCTION_CAPITAL_HARD_CAP_USD
    )
    if spec.capital_limit_usd > capital_hard_cap:
        raise ValueError("release capital exceeds the immutable stage hard cap")
    if settings.risk.max_inventory_notional_usd > spec.capital_limit_usd:
        raise ValueError("release inventory limit exceeds approved capital")
    if spec.stage is PromotionStage.PRODUCTION:
        canary = CanaryEvidenceReport.model_validate_json(
            payloads[DeploymentArtifactKind.CANARY_EVIDENCE]
        )
        _require_complete_gates(
            (gate.gate for gate in canary.gates),
            expected=CANARY_REQUIRED_GATES,
            label="canary evidence",
        )
        if not canary.awaiting_production_approval or not all(gate.passed for gate in canary.gates):
            raise ValueError("canary evidence has not reached the production approval boundary")
        if canary.deployment_id != spec.rollback_deployment_id:
            raise ValueError("canary evidence does not bind the production rollback deployment")


def _live_feature_config_payload(bundle: ConfigBundle, settings: NativeSettings) -> bytes:
    root = bundle.sources[0].parent.resolve(strict=True)
    path = (root / settings.live_strategy.feature_config_path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("release live feature configuration escapes its configuration root")
    return _read_regular(path, maximum_bytes=MAX_RELEASE_ARTIFACT_BYTES)


def _strategy_configuration(payload: bytes) -> StrategyConfiguration:
    return STRATEGY_ADAPTER.validate_python(_toml_configuration(payload, label="release strategy"))


def _toml_configuration(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        parsed = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{label} configuration is not valid UTF-8 TOML") from exc
    if not isinstance(parsed, dict):  # pragma: no cover - tomllib always returns a dict
        raise ValueError(f"{label} configuration must contain a TOML table")
    return parsed


def _require_complete_gates(
    gates: Iterable[str],
    *,
    expected: frozenset[str],
    label: str,
) -> None:
    names = tuple(gates)
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError(f"{label} gate set is incomplete or ambiguous")


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open release artifact: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"release artifact is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ValueError(f"release artifact size is invalid: {path.name}")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final_metadata = os.fstat(descriptor)
        identity = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(payload) != metadata.st_size or any(
            getattr(metadata, field) != getattr(final_metadata, field) for field in identity
        ):
            raise ValueError(f"release artifact changed while read: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _write_bundle(
    *,
    parent: Path,
    output_dir: Path,
    artifacts: tuple[PreparedArtifact, ...],
    manifest_payload: bytes,
    approval_payload: bytes,
    behavior_payload: bytes,
    receipt_payload: bytes,
) -> None:
    temporary = parent / f".{output_dir.name}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir(mode=0o700)
    try:
        artifacts_dir = temporary / "artifacts"
        artifacts_dir.mkdir(mode=0o700)
        for artifact in artifacts:
            _write_new(temporary / artifact.relative_path, artifact.payload)
        _write_new(temporary / "artifact-manifest.json", manifest_payload)
        _write_new(temporary / "deployment-approval.unsigned.json", approval_payload)
        _write_new(temporary / "behavior-configuration.json", behavior_payload)
        _write_new(temporary / "release-bundle-receipt.json", receipt_payload)
        _fsync_directory(artifacts_dir)
        _fsync_directory(temporary)
        temporary.rename(output_dir)
        _fsync_directory(parent)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _digest(relative_path: str, payload: bytes) -> ReleaseFileDigest:
    return ReleaseFileDigest(
        relative_path=relative_path,
        content_sha256=_sha(payload),
        byte_count=len(payload),
    )


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
