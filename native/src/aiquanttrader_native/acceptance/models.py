"""Typed inputs for a complete, immutable testnet acceptance evidence bundle."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel
from aiquanttrader_native.domain.execution import RiskReason, RiskState
from aiquanttrader_native.governance.models import TestnetLifecycleScenario

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EthereumAddress = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class EvidenceCategory(StrEnum):
    EXECUTION_JOURNAL = "execution_journal"
    EXECUTION_AUDIT = "execution_audit"
    SENTINEL_AUDIT = "sentinel_audit"
    EXECUTION_METRICS = "execution_metrics"
    SENTINEL_METRICS = "sentinel_metrics"
    VENUE_ORDERS = "venue_orders"
    VENUE_FILLS = "venue_fills"
    VENUE_ACCOUNT = "venue_account"
    KILL_SWITCH_AUDIT = "kill_switch_audit"
    PROCESS_EVENTS = "process_events"
    CONFIG_INSPECTION = "config_inspection"


REQUIRED_EVIDENCE_CATEGORIES = frozenset(EvidenceCategory)


class EvidenceArtifactBinding(DomainModel):
    category: EvidenceCategory
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    content_sha256: Sha256
    byte_count: int = Field(gt=0, le=67_108_864)
    captured_start_ts_ns: int = Field(ge=0)
    captured_end_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def safe_path_and_interval(self) -> Self:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "raw":
            raise ValueError("acceptance artifacts must use traversal-free paths below raw/")
        if self.captured_end_ts_ns < self.captured_start_ts_ns:
            raise ValueError("artifact capture interval is reversed")
        return self


class TestnetAcceptanceRunManifest(DomainModel):
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
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    trading_wallet_address: EthereumAddress
    control_wallet_address: EthereumAddress
    artifacts: tuple[EvidenceArtifactBinding, ...] = Field(
        min_length=len(REQUIRED_EVIDENCE_CATEGORIES),
        max_length=len(REQUIRED_EVIDENCE_CATEGORIES),
    )

    @model_validator(mode="after")
    def exact_identity_and_inventory(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("acceptance run must have a positive interval")
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
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("acceptance artifact paths must be unique")
        categories = {artifact.category for artifact in self.artifacts}
        if categories != REQUIRED_EVIDENCE_CATEGORIES:
            raise ValueError("acceptance artifact categories are incomplete or unexpected")
        for artifact in self.artifacts:
            if artifact.captured_start_ts_ns < self.started_ts_ns:
                raise ValueError("artifact capture starts before the acceptance run")
            if artifact.captured_end_ts_ns > self.ended_ts_ns:
                raise ValueError("artifact capture ends after the acceptance run")
        return self


class TestnetScenarioCheck(DomainModel):
    check_id: Identifier
    passed: bool
    actual: Annotated[str, Field(min_length=1, max_length=1_024)]
    required: Annotated[str, Field(min_length=1, max_length=1_024)]


class TestnetScenarioEvidence(DomainModel):
    schema_version: Literal[1] = 1
    scenario: TestnetLifecycleScenario
    started_ts_ns: int = Field(ge=0)
    ended_ts_ns: int = Field(ge=0)
    checks: tuple[TestnetScenarioCheck, ...] = Field(min_length=1, max_length=64)
    artifact_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        min_length=1,
        max_length=32,
    )
    invalidating_events: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def interval_and_checks_are_unambiguous(self) -> Self:
        if self.ended_ts_ns <= self.started_ts_ns:
            raise ValueError("scenario evidence must have a positive interval")
        check_ids = [check.check_id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("scenario check identities must be unique")
        if len(self.artifact_paths) != len(set(self.artifact_paths)):
            raise ValueError("scenario artifact references must be unique")
        for value in self.artifact_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("scenario artifact references cannot traverse")
        if len(self.invalidating_events) != len(set(self.invalidating_events)):
            raise ValueError("scenario invalidating events must be unique")
        return self

    @property
    def passed(self) -> bool:
        return not self.invalidating_events and all(check.passed for check in self.checks)


class TestnetFinalVenueState(DomainModel):
    schema_version: Literal[1] = 1
    captured_ts_ns: int = Field(ge=0)
    network: Literal["testnet"] = "testnet"
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    account_address: EthereumAddress
    vault_address: EthereumAddress | None = None
    position_base: Decimal
    open_order_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def orders_are_unique(self) -> Self:
        if len(self.open_order_ids) != len(set(self.open_order_ids)):
            raise ValueError("final venue order identities must be unique")
        return self


class TestnetOperationalFacts(DomainModel):
    schema_version: Literal[1] = 1
    reconciliation_failures: int = Field(ge=0)
    risk_breaches: int = Field(ge=0)
    deadman_cancellations: int = Field(ge=0)
    mainnet_credentials_present: Literal[False] = False
    artifact_paths: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = Field(
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def sources_are_unique_and_safe(self) -> Self:
        if len(self.artifact_paths) != len(set(self.artifact_paths)):
            raise ValueError("operational fact artifact references must be unique")
        for value in self.artifact_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("operational fact artifact references cannot traverse")
        return self


class AcceptanceComponent(StrEnum):
    EXECUTION = "execution"
    SENTINEL = "sentinel"


class OperationalEventKind(StrEnum):
    RECONCILIATION = "reconciliation"
    RISK_STATE = "risk_state"
    EXECUTION_CANCEL_ALL = "execution_cancel_all"
    LIVE_PIPELINE_FAULT = "live_pipeline_fault"
    HEARTBEAT_STATE = "heartbeat_state"
    DEADMAN_SCHEDULE = "deadman_schedule"
    SENTINEL_EMERGENCY_CANCEL = "sentinel_emergency_cancel"


class OperationalEvidenceEvent(DomainModel):
    schema_version: Literal[1] = 1
    event_id: Identifier
    sequence: int = Field(gt=0)
    prior_event_sha256: Sha256 | None = None
    component: AcceptanceComponent
    kind: OperationalEventKind
    event_ts_ns: int = Field(ge=0)
    success: bool
    order_count: int | None = Field(default=None, ge=0)
    risk_state: RiskState | None = None
    risk_reasons: tuple[RiskReason, ...] = ()
    detail: Annotated[str, Field(min_length=1, max_length=1_024)]

    @model_validator(mode="after")
    def component_matches_event(self) -> Self:
        execution_kinds = {
            OperationalEventKind.RECONCILIATION,
            OperationalEventKind.RISK_STATE,
            OperationalEventKind.EXECUTION_CANCEL_ALL,
            OperationalEventKind.LIVE_PIPELINE_FAULT,
        }
        if self.component is AcceptanceComponent.EXECUTION and self.kind not in execution_kinds:
            raise ValueError("execution audit contains a sentinel-only event")
        if self.component is AcceptanceComponent.SENTINEL and self.kind in execution_kinds:
            raise ValueError("sentinel audit contains an execution-only event")
        if self.sequence == 1 and self.prior_event_sha256 is not None:
            raise ValueError("first operational event cannot have a predecessor")
        if self.sequence > 1 and self.prior_event_sha256 is None:
            raise ValueError("later operational events require a predecessor hash")
        if self.kind is OperationalEventKind.RISK_STATE and self.risk_state is None:
            raise ValueError("risk-state events require the observed state")
        if self.kind is not OperationalEventKind.RISK_STATE and self.risk_state is not None:
            raise ValueError("only risk-state events may include risk state")
        if self.kind is not OperationalEventKind.RISK_STATE and self.risk_reasons:
            raise ValueError("only risk-state events may include risk reasons")
        if len(self.risk_reasons) != len(set(self.risk_reasons)):
            raise ValueError("operational risk reasons must be unique")
        if self.kind is OperationalEventKind.RISK_STATE:
            if self.risk_state is RiskState.ACTIVE and self.risk_reasons:
                raise ValueError("active risk-state events cannot contain risk reasons")
            if self.risk_state is not RiskState.ACTIVE and not self.risk_reasons:
                raise ValueError("non-active risk-state events require typed risk reasons")
        return self
