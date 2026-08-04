"""Immutable research and deployment governance schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader_native.domain.base import DomainModel

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PromotionStage(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    BACKTEST_PASSED = "backtest_passed"
    WALK_FORWARD_PASSED = "walk_forward_passed"
    PAPER_PASSED = "paper_passed"
    SHADOW_PASSED = "shadow_passed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED_CANARY = "approved_canary"
    PRODUCTION = "production"
    RETIRED = "retired"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ActorKind(StrEnum):
    AUTOMATION = "automation"
    HUMAN_APPROVER = "human_approver"
    SAFETY_CONTROLLER = "safety_controller"


ADVANCE_TRANSITIONS: dict[PromotionStage, frozenset[PromotionStage]] = {
    PromotionStage.DRAFT: frozenset({PromotionStage.CANDIDATE, PromotionStage.REJECTED}),
    PromotionStage.CANDIDATE: frozenset({PromotionStage.BACKTEST_PASSED, PromotionStage.REJECTED}),
    PromotionStage.BACKTEST_PASSED: frozenset(
        {PromotionStage.WALK_FORWARD_PASSED, PromotionStage.REJECTED}
    ),
    PromotionStage.WALK_FORWARD_PASSED: frozenset(
        {PromotionStage.PAPER_PASSED, PromotionStage.REJECTED}
    ),
    PromotionStage.PAPER_PASSED: frozenset({PromotionStage.SHADOW_PASSED, PromotionStage.REJECTED}),
    PromotionStage.SHADOW_PASSED: frozenset(
        {PromotionStage.AWAITING_APPROVAL, PromotionStage.REJECTED}
    ),
    PromotionStage.AWAITING_APPROVAL: frozenset(
        {PromotionStage.APPROVED_CANARY, PromotionStage.REJECTED}
    ),
    PromotionStage.APPROVED_CANARY: frozenset(
        {PromotionStage.PRODUCTION, PromotionStage.ROLLED_BACK}
    ),
    PromotionStage.PRODUCTION: frozenset({PromotionStage.RETIRED, PromotionStage.ROLLED_BACK}),
    PromotionStage.RETIRED: frozenset(),
    PromotionStage.REJECTED: frozenset(),
    PromotionStage.ROLLED_BACK: frozenset(),
}


def validate_stage_transition(
    current: PromotionStage,
    target: PromotionStage,
    actor: ActorKind,
) -> None:
    """Enforce legal transitions and the human production boundary."""

    if target not in ADVANCE_TRANSITIONS[current]:
        raise ValueError(f"illegal promotion transition: {current.value} -> {target.value}")
    if (
        target in {PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION}
        and actor is not ActorKind.HUMAN_APPROVER
    ):
        raise ValueError(f"{target.value} requires a human approver")
    if actor is ActorKind.SAFETY_CONTROLLER and target not in {
        PromotionStage.REJECTED,
        PromotionStage.ROLLED_BACK,
        PromotionStage.RETIRED,
    }:
        raise ValueError("safety controller may only reduce deployment authority")


class ExperimentManifest(DomainModel):
    schema_version: Literal[1] = 1
    experiment_id: Annotated[str, Field(min_length=1, max_length=128)]
    created_at: datetime
    stage: PromotionStage
    code_sha256: Sha256
    dataset_sha256: Sha256
    feature_schema_sha256: Sha256
    configuration_sha256: Sha256
    parameters: dict[str, Any]
    metrics: dict[str, int | float | str | bool | None]

    @model_validator(mode="after")
    def created_at_must_be_aware(self) -> ExperimentManifest:
        if self.created_at.tzinfo is None:
            raise ValueError("experiment timestamp must be timezone-aware")
        return self


class DeploymentApproval(DomainModel):
    schema_version: Literal[1] = 1
    approval_id: Annotated[str, Field(min_length=1, max_length=128)]
    stage: Literal[PromotionStage.APPROVED_CANARY, PromotionStage.PRODUCTION]
    account_address: Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
    instrument_id: Literal["BTC-USD-PERP.HYPERLIQUID"] = "BTC-USD-PERP.HYPERLIQUID"
    commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    image_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    artifact_manifest_sha256: Sha256
    model_sha256: Sha256
    configuration_sha256: Sha256
    feature_schema_sha256: Sha256
    capital_limit_usd: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*(\.[0-9]+)?$")]
    risk_policy_sha256: Sha256
    rollback_deployment_id: Annotated[str, Field(min_length=1, max_length=128)]
    approver: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_approval_window(self) -> DeploymentApproval:
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("approval timestamps must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("approval expiry must follow approval time")
        return self

    def is_active(self, now: datetime | None = None) -> bool:
        instant = datetime.now(UTC) if now is None else now
        if instant.tzinfo is None:
            raise ValueError("approval check timestamp must be timezone-aware")
        return self.approved_at <= instant < self.expires_at
