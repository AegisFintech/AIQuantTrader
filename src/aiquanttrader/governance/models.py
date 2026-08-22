"""Versioned production-admission, artifact, and canary evidence contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.config.models import RiskLimits
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader.research.models import ModelArtifactManifest

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


class DeploymentAuthorizationRenewal(DomainModel):
    """Short-lived authority to extend one unchanged production admission."""

    schema_version: Literal[1] = 1
    renewal_id: Identifier
    deployment_id: Identifier
    initial_approval_id: Identifier
    admission_id: Sha256
    prior_authorization_id: Sha256
    stage: Literal[PromotionStage.PRODUCTION] = PromotionStage.PRODUCTION
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    artifact_manifest_sha256: Sha256
    configuration_sha256: Sha256
    image_digest: ImageDigest
    capital_limit_usd: Annotated[Decimal, Field(gt=0)]
    approver: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def window_and_identity_are_valid(self) -> Self:
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("deployment renewal timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("deployment renewal expiry must follow approval time")
        if self.expires_at - self.approved_at > timedelta(days=7):
            raise ValueError("deployment renewal cannot remain valid over seven days")
        if self.vault_address is not None and (
            self.vault_address.lower() == self.account_address.lower()
        ):
            raise ValueError("deployment renewal vault and account must differ")
        return self

    def is_active(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("deployment renewal check timestamp must be timezone-aware")
        return self.approved_at <= now < self.expires_at


class VerifiedDeploymentRenewal(DomainModel):
    """Signature-verified renewal for one exact active admission."""

    schema_version: Literal[1] = 1
    authorization_id: Sha256
    renewal: DeploymentAuthorizationRenewal
    public_key_sha256: Sha256
    signature_envelope_sha256: Sha256
    verified_at: datetime

    @model_validator(mode="after")
    def identity_matches(self) -> Self:
        if self.verified_at.tzinfo is None:
            raise ValueError("deployment renewal verification timestamp must be timezone-aware")
        identity = self.model_dump(mode="json", exclude={"authorization_id", "verified_at"})
        if canonical_sha256(identity) != self.authorization_id:
            raise ValueError("deployment renewal authorization identity does not match")
        if not self.renewal.is_active(self.verified_at):
            raise ValueError("deployment renewal is not active at verification")
        return self


class DeploymentAdmissionState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"
    REVOKED = "revoked"


class DeploymentAdmissionRecord(DomainModel):
    schema_version: Literal[2] = 2
    deployment_id: Identifier
    approval_id: Identifier
    admission_id: Sha256
    authorization_id: Sha256
    renewal_count: int = Field(ge=0)
    approval_public_key_sha256: Sha256 | None = None
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

    @model_validator(mode="after")
    def authorization_and_window_are_valid(self) -> Self:
        if self.admitted_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("deployment admission timestamps must be timezone-aware")
        if self.expires_at <= self.admitted_at:
            raise ValueError("deployment admission expiry must follow admission time")
        if self.renewal_count == 0 and self.authorization_id != self.admission_id:
            raise ValueError("initial deployment authorization must equal the admission identity")
        if self.renewal_count > 0 and self.authorization_id == self.admission_id:
            raise ValueError("renewed deployment authorization must bind a renewal identity")
        if self.renewal_count > 0 and self.approval_public_key_sha256 is None:
            raise ValueError("renewed deployment authorization requires a bound trust root")
        return self


class TestnetLifecycleScenario(StrEnum):
    PASSIVE_POST_ONLY = "passive_post_only"
    CROSSING_POST_ONLY_REJECT = "crossing_post_only_reject"
    NON_MARKETABLE_IOC = "non_marketable_ioc"
    MARKETABLE_IOC = "marketable_ioc"
    CANCEL_REPLACE = "cancel_replace"
    PARTIAL_FILL_CANCEL = "partial_fill_cancel"
    REDUCE_ONLY = "reduce_only"
    DUPLICATE_INTENT = "duplicate_intent"
    UNKNOWN_OUTCOME_RECONCILIATION = "unknown_outcome_reconciliation"
    NODE_RESTART_RECONCILIATION = "node_restart_reconciliation"
    STALE_DATA_KILL = "stale_data_kill"
    LOSS_DRAWDOWN_REDUCE_ONLY = "loss_drawdown_reduce_only"
    OPERATOR_KILL = "operator_kill"
    TRADING_NODE_DEATH = "trading_node_death"
    SENTINEL_DEATH = "sentinel_death"


class TestnetScenarioResult(DomainModel):
    scenario: TestnetLifecycleScenario
    passed: bool
    evidence_sha256: Sha256


class TestnetEvidenceGate(StrEnum):
    POLICY_FROZEN = "policy_frozen"
    OBSERVATION = "observation"
    ORDERS = "orders"
    FILLS = "fills"
    SCENARIO_MATRIX = "scenario_matrix"
    SCENARIO_RESULTS = "scenario_results"
    UNKNOWN_OUTCOMES_RESOLVED = "unknown_outcomes_resolved"
    RECONCILIATION_FAILURES = "reconciliation_failures"
    DUPLICATE_VENUE_ORDERS = "duplicate_venue_orders"
    RISK_BREACHES = "risk_breaches"
    CANCEL_ALL = "cancel_all"
    DEADMAN_CANCELLATION = "deadman_cancellation"
    FLAT_FINAL_STATE = "flat_final_state"
    NO_MAINNET_CREDENTIALS = "no_mainnet_credentials"


class TestnetDressRehearsalPolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_observation_ns: int = Field(gt=0)
    minimum_orders: int = Field(gt=0)
    minimum_fills: int = Field(gt=0)
    minimum_cancel_all_confirmations: int = Field(gt=0)
    minimum_deadman_cancellations: int = Field(gt=0)
    required_scenarios: tuple[TestnetLifecycleScenario, ...] = Field(
        min_length=len(TestnetLifecycleScenario),
        max_length=len(TestnetLifecycleScenario),
    )

    @model_validator(mode="after")
    def complete_scenario_matrix(self) -> Self:
        if set(self.required_scenarios) != set(TestnetLifecycleScenario):
            raise ValueError("testnet policy must freeze the complete lifecycle matrix")
        return self


class TestnetDressRehearsalObservation(DomainModel):
    schema_version: Literal[1] = 1
    rehearsal_id: Identifier
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    image_digest: ImageDigest
    dependency_lock_sha256: Sha256
    dataset_sha256: Sha256
    model_sha256: Sha256
    feature_schema_sha256: Sha256
    strategy_config_sha256: Sha256
    risk_policy_sha256: Sha256
    target_configuration_sha256: Sha256
    network: Literal["testnet"] = "testnet"
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    trading_wallet_address: EthereumAddress
    control_wallet_address: EthereumAddress
    mainnet_credentials_present: Literal[False] = False
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    unknown_outcomes: int = Field(ge=0)
    resolved_unknown_outcomes: int = Field(ge=0)
    reconciliation_failures: int = Field(ge=0)
    duplicate_venue_orders: int = Field(ge=0)
    risk_breaches: int = Field(ge=0)
    cancel_all_confirmations: int = Field(ge=0)
    deadman_cancellations: int = Field(ge=0)
    ending_position_base: Decimal
    ending_open_orders: int = Field(ge=0)
    scenarios: tuple[TestnetScenarioResult, ...] = Field(min_length=1)
    evidence_bundle_sha256: Sha256

    @model_validator(mode="after")
    def identities_counts_and_scenarios_reconcile(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("testnet rehearsal must have a positive interval")
        if self.fills > self.orders:
            raise ValueError("testnet fills cannot exceed submitted orders")
        if self.resolved_unknown_outcomes > self.unknown_outcomes:
            raise ValueError("resolved unknown outcomes cannot exceed observed outcomes")
        if self.trading_wallet_address.lower() == self.control_wallet_address.lower():
            raise ValueError("testnet trading and control wallets must differ")
        if self.vault_address is not None and (
            self.vault_address.lower() == self.account_address.lower()
        ):
            raise ValueError("testnet vault and account identities must differ")
        identities = {
            address.lower()
            for address in (
                self.account_address,
                self.vault_address,
                self.trading_wallet_address,
                self.control_wallet_address,
            )
            if address is not None
        }
        expected_identities = 4 if self.vault_address is not None else 3
        if len(identities) != expected_identities:
            raise ValueError("testnet account, vault, and wallet identities must be distinct")
        scenario_ids = [result.scenario for result in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("testnet scenario results must be unique")
        return self


class TestnetGateResult(DomainModel):
    gate: TestnetEvidenceGate
    passed: bool
    actual: str
    required: str


class TestnetDressRehearsalReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    rehearsal_id: Identifier
    policy_id: Identifier
    policy_sha256: Sha256
    observation_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    image_digest: ImageDigest
    dependency_lock_sha256: Sha256
    dataset_sha256: Sha256
    model_sha256: Sha256
    feature_schema_sha256: Sha256
    strategy_config_sha256: Sha256
    risk_policy_sha256: Sha256
    target_configuration_sha256: Sha256
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    trading_wallet_address: EthereumAddress
    control_wallet_address: EthereumAddress
    observation_ns: int = Field(ge=0)
    orders: int = Field(ge=0)
    fills: int = Field(ge=0)
    gates: tuple[TestnetGateResult, ...]
    awaiting_canary_approval: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"report_id", "awaiting_canary_approval"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("testnet rehearsal report identity does not match")
        gate_names = [gate.gate for gate in self.gates]
        if len(gate_names) != len(TestnetEvidenceGate) or set(gate_names) != set(
            TestnetEvidenceGate
        ):
            raise ValueError("testnet rehearsal report must contain the complete gate set")
        if self.awaiting_canary_approval != all(gate.passed for gate in self.gates):
            raise ValueError("testnet rehearsal verdict does not match its gates")
        return self


class ReleaseArtifactSourcePaths(DomainModel):
    dependency_lock: Path
    dataset_manifest: Path
    model_manifest: Path
    feature_schema: Path
    strategy_config: Path
    shadow_evidence: Path
    testnet_evidence: Path
    canary_evidence: Path | None = None

    @model_validator(mode="after")
    def paths_are_absolute_and_unique(self) -> Self:
        paths = tuple(
            path
            for path in (
                self.dependency_lock,
                self.dataset_manifest,
                self.model_manifest,
                self.feature_schema,
                self.strategy_config,
                self.shadow_evidence,
                self.testnet_evidence,
                self.canary_evidence,
            )
            if path is not None
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError("release artifact source paths must be absolute")
        if len(paths) != len(set(paths)):
            raise ValueError("release artifact source paths must be unique")
        return self


class DeploymentModelSelection(DomainModel):
    schema_version: Literal[1] = 1
    selection: Literal["none", "trained"]
    strategy_id: Literal["avellaneda-stoikov-v1", "order-flow-scalper-v1"]
    feature_schema_sha256: Sha256
    model: ModelArtifactManifest | None = None

    @model_validator(mode="after")
    def selection_matches_model(self) -> Self:
        if self.selection == "none" and self.model is not None:
            raise ValueError("no-model selection cannot embed a trained model")
        if self.selection == "trained" and self.model is None:
            raise ValueError("trained-model selection requires a model manifest")
        if self.model is not None and (
            self.model.feature_schema_sha256 != self.feature_schema_sha256
        ):
            raise ValueError("selected model feature schema does not match release schema")
        return self


class ReleaseBundleSpec(DomainModel):
    schema_version: Literal[1] = 1
    deployment_id: Identifier
    approval_id: Identifier
    stage: Literal[PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION]
    rollback_deployment_id: Identifier
    prior_approval_id: Identifier | None = None
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    image_digest: ImageDigest
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    trading_wallet_address: EthereumAddress
    control_wallet_address: EthereumAddress
    capital_limit_usd: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
    approver: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: datetime
    expires_at: datetime
    risk: RiskLimits
    artifacts: ReleaseArtifactSourcePaths

    @model_validator(mode="after")
    def stage_and_identity_are_consistent(self) -> Self:
        if self.deployment_id == self.rollback_deployment_id:
            raise ValueError("release rollback target must differ from deployment")
        if self.stage is PromotionStage.APPROVED_CANARY:
            if self.prior_approval_id is not None or self.artifacts.canary_evidence is not None:
                raise ValueError("canary release cannot bind prior approval or canary evidence")
        elif self.prior_approval_id is None or self.artifacts.canary_evidence is None:
            raise ValueError("production release requires prior approval and canary evidence")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("release approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("release approval expiry must follow approval time")
        if self.expires_at - self.approved_at > timedelta(days=7):
            raise ValueError("release approval cannot remain valid for more than seven days")
        if self.trading_wallet_address.lower() == self.control_wallet_address.lower():
            raise ValueError("release trading and control wallets must differ")
        if self.vault_address is not None and (
            self.vault_address.lower() == self.account_address.lower()
        ):
            raise ValueError("release vault and account identities must differ")
        identities = {
            address.lower()
            for address in (
                self.account_address,
                self.vault_address,
                self.trading_wallet_address,
                self.control_wallet_address,
            )
            if address is not None
        }
        expected_identities = 4 if self.vault_address is not None else 3
        if len(identities) != expected_identities:
            raise ValueError("release account, vault, and wallet identities must be distinct")
        return self


class ReleaseFileDigest(DomainModel):
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0)

    @model_validator(mode="after")
    def path_is_safe(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
            raise ValueError("release receipt path must be safe and relative")
        return self


class ReleaseBundleReceipt(DomainModel):
    schema_version: Literal[1] = 1
    receipt_id: Sha256
    deployment_id: Identifier
    stage: Literal[PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION]
    artifact_manifest_sha256: Sha256
    unsigned_approval_sha256: Sha256
    behavior_configuration_sha256: Sha256
    files: tuple[ReleaseFileDigest, ...] = Field(min_length=11)
    awaiting_offline_signature: Literal[True] = True

    @model_validator(mode="after")
    def identity_and_files_match(self) -> Self:
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("release receipt file paths must be unique")
        identity = self.model_dump(mode="json", exclude={"receipt_id"})
        if canonical_sha256(identity) != self.receipt_id:
            raise ValueError("release bundle receipt identity does not match")
        return self


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
