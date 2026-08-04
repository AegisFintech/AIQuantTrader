"""Versioned production-admission, artifact, and canary evidence contracts."""

from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.domain.governance import DeploymentApproval, PromotionStage

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
EthereumAddress = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]


class DeploymentArtifactKind(StrEnum):
    DEPENDENCY_LOCK = "dependency_lock"
    DATASET_MANIFEST = "dataset_manifest"
    MODEL_MANIFEST = "model_manifest"
    FEATURE_SCHEMA = "feature_schema"
    STRATEGY_CONFIG = "strategy_config"
    RISK_POLICY = "risk_policy"
    SHADOW_EVIDENCE = "shadow_evidence"
    TESTNET_EVIDENCE = "testnet_evidence"
    CANARY_EVIDENCE = "canary_evidence"


class DeploymentArtifactBinding(DomainModel):
    kind: DeploymentArtifactKind
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256

    @model_validator(mode="after")
    def safe_relative_path(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
            raise ValueError("deployment artifact path must be relative and cannot traverse")
        return self


class DeploymentArtifactManifest(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    stage: Literal[PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION]
    created_at: datetime
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    image_digest: ImageDigest
    configuration_sha256: Sha256
    dependency_lock_sha256: Sha256
    dataset_sha256: Sha256
    model_sha256: Sha256
    feature_schema_sha256: Sha256
    strategy_config_sha256: Sha256
    risk_policy_sha256: Sha256
    shadow_evidence_sha256: Sha256
    testnet_evidence_sha256: Sha256
    canary_evidence_sha256: Sha256 | None = None
    rollback_deployment_id: Identifier
    artifacts: tuple[DeploymentArtifactBinding, ...] = Field(min_length=8, max_length=9)

    @model_validator(mode="after")
    def artifact_set_is_complete(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("artifact manifest timestamp must be timezone-aware")
        if self.deployment_id == self.rollback_deployment_id:
            raise ValueError("artifact rollback target must differ from deployment")
        by_kind = {binding.kind: binding for binding in self.artifacts}
        if len(by_kind) != len(self.artifacts):
            raise ValueError("deployment artifact kinds must be unique")
        expected: dict[DeploymentArtifactKind, str] = {
            DeploymentArtifactKind.DEPENDENCY_LOCK: self.dependency_lock_sha256,
            DeploymentArtifactKind.DATASET_MANIFEST: self.dataset_sha256,
            DeploymentArtifactKind.MODEL_MANIFEST: self.model_sha256,
            DeploymentArtifactKind.FEATURE_SCHEMA: self.feature_schema_sha256,
            DeploymentArtifactKind.STRATEGY_CONFIG: self.strategy_config_sha256,
            DeploymentArtifactKind.RISK_POLICY: self.risk_policy_sha256,
            DeploymentArtifactKind.SHADOW_EVIDENCE: self.shadow_evidence_sha256,
            DeploymentArtifactKind.TESTNET_EVIDENCE: self.testnet_evidence_sha256,
        }
        if self.stage is PromotionStage.PRODUCTION:
            if self.canary_evidence_sha256 is None:
                raise ValueError("production artifact manifest requires canary evidence")
            expected[DeploymentArtifactKind.CANARY_EVIDENCE] = self.canary_evidence_sha256
        elif self.canary_evidence_sha256 is not None:
            raise ValueError("canary artifact manifest cannot bind canary evidence")
        if set(by_kind) != set(expected):
            raise ValueError("deployment artifact set does not match its stage")
        for kind, digest in expected.items():
            if by_kind[kind].content_sha256 != digest:
                raise ValueError(f"deployment artifact digest mismatch for {kind.value}")
        return self


class DetachedApprovalSignature(DomainModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["ed25519"] = "ed25519"
    key_id: Identifier
    approval_sha256: Sha256
    signature_base64: Annotated[str, Field(min_length=88, max_length=88)]

    @model_validator(mode="after")
    def signature_is_canonical(self) -> Self:
        try:
            decoded = base64.b64decode(self.signature_base64, validate=True)
        except ValueError as exc:
            raise ValueError("approval signature is not canonical base64") from exc
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != self.signature_base64:
            raise ValueError("approval signature must be one canonical Ed25519 signature")
        return self

    def signature_bytes(self) -> bytes:
        return base64.b64decode(self.signature_base64, validate=True)


class VerifiedDeploymentAdmission(DomainModel):
    schema_version: Literal[1] = 1
    admission_id: Sha256
    approval: DeploymentApproval
    artifact_manifest: DeploymentArtifactManifest
    public_key_sha256: Sha256
    signature_envelope_sha256: Sha256
    verified_at: datetime

    @model_validator(mode="after")
    def identity_matches(self) -> Self:
        if self.verified_at.tzinfo is None:
            raise ValueError("admission verification timestamp must be timezone-aware")
        payload = self.model_dump(mode="json", exclude={"admission_id", "verified_at"})
        if canonical_sha256(payload) != self.admission_id:
            raise ValueError("deployment admission identity does not match")
        return self


class DeploymentAdmissionState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"
    REVOKED = "revoked"


class DeploymentAdmissionRecord(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    approval_id: Identifier
    admission_id: Sha256
    stage: Literal[PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION]
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    artifact_manifest_sha256: Sha256
    configuration_sha256: Sha256
    image_digest: ImageDigest
    capital_limit_usd: Annotated[Decimal, Field(gt=0)]
    admitted_at: datetime
    expires_at: datetime
    state: DeploymentAdmissionState
    actor: Annotated[str, Field(min_length=1, max_length=256)]
    reason: Annotated[str, Field(min_length=1, max_length=512)]


class CanaryEvidencePolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_observation_ns: int = Field(gt=0)
    minimum_orders: int = Field(gt=0)
    minimum_fills: int = Field(gt=0)
    minimum_maker_fills: int = Field(gt=0)
    maximum_drawdown_fraction: Annotated[Decimal, Field(gt=0, le=1)]
    maximum_rejection_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    maximum_adverse_markout_bps: Annotated[Decimal, Field(ge=0)]
    require_positive_post_cost_pnl: bool = True
    required_drills: tuple[
        Literal[
            "operator_kill",
            "deadman_expiry",
            "restart_reconciliation",
            "credential_rotation",
            "backup_restore",
        ],
        ...,
    ] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def drills_are_unique(self) -> Self:
        if len(set(self.required_drills)) != len(self.required_drills):
            raise ValueError("canary drills must be unique")
        return self


class CanaryObservation(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    admission_id: Sha256
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    maker_fills: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    unknown_outcomes: int = Field(ge=0)
    reconciliation_failures: int = Field(ge=0)
    fee_events: int = Field(ge=0)
    funding_events: int = Field(ge=0)
    post_cost_pnl_usd: Decimal
    maximum_drawdown_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    mean_adverse_markout_bps: Decimal
    maximum_account_equity_usd: Annotated[Decimal, Field(gt=0)]
    completed_drills: tuple[Identifier, ...]
    evidence_bundle_sha256: Sha256

    @model_validator(mode="after")
    def interval_and_counts_reconcile(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("canary observation must have a positive interval")
        if self.fills > self.orders or self.maker_fills > self.fills:
            raise ValueError("canary order/fill counts do not reconcile")
        if self.rejected_orders > self.orders:
            raise ValueError("canary rejection count exceeds submitted orders")
        return self


class CanaryGateResult(DomainModel):
    gate: Identifier
    passed: bool
    actual: str
    required: str


class CanaryEvidenceReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    deployment_id: Identifier
    admission_id: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    observation_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    gates: tuple[CanaryGateResult, ...]
    awaiting_production_approval: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        identity = self.model_dump(
            mode="json", exclude={"report_id", "awaiting_production_approval"}
        )
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("canary evidence report identity does not match")
        if self.awaiting_production_approval != all(gate.passed for gate in self.gates):
            raise ValueError("canary evidence verdict does not match its gates")
        return self
