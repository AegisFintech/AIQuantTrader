from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aiquanttrader_native.domain.governance import (
    ActorKind,
    DeploymentApproval,
    PromotionStage,
    validate_stage_transition,
)

HASH = "a" * 64


def approval(**overrides: object) -> DeploymentApproval:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    values: dict[str, object] = {
        "approval_id": "approval-001",
        "stage": PromotionStage.APPROVED_CANARY,
        "account_address": "0x" + "1" * 40,
        "commit_sha": "b" * 40,
        "image_digest": "sha256:" + "c" * 64,
        "artifact_manifest_sha256": HASH,
        "model_sha256": HASH,
        "configuration_sha256": HASH,
        "feature_schema_sha256": HASH,
        "capital_limit_usd": "1000",
        "risk_policy_sha256": HASH,
        "rollback_deployment_id": "deployment-previous",
        "approver": "risk-owner@example.invalid",
        "approved_at": now,
        "expires_at": now + timedelta(days=1),
    }
    values.update(overrides)
    return DeploymentApproval.model_validate(values)


def test_automation_can_advance_only_to_approval_boundary() -> None:
    validate_stage_transition(
        PromotionStage.SHADOW_PASSED,
        PromotionStage.AWAITING_APPROVAL,
        ActorKind.AUTOMATION,
    )
    with pytest.raises(ValueError, match="human approver"):
        validate_stage_transition(
            PromotionStage.AWAITING_APPROVAL,
            PromotionStage.APPROVED_CANARY,
            ActorKind.AUTOMATION,
        )


def test_human_can_approve_canary_and_production() -> None:
    validate_stage_transition(
        PromotionStage.AWAITING_APPROVAL,
        PromotionStage.APPROVED_CANARY,
        ActorKind.HUMAN_APPROVER,
    )
    validate_stage_transition(
        PromotionStage.APPROVED_CANARY,
        PromotionStage.PRODUCTION,
        ActorKind.HUMAN_APPROVER,
    )


def test_safety_controller_cannot_increase_authority() -> None:
    with pytest.raises(ValueError, match="safety controller"):
        validate_stage_transition(
            PromotionStage.DRAFT,
            PromotionStage.CANDIDATE,
            ActorKind.SAFETY_CONTROLLER,
        )
    validate_stage_transition(
        PromotionStage.PRODUCTION,
        PromotionStage.ROLLED_BACK,
        ActorKind.SAFETY_CONTROLLER,
    )


def test_illegal_stage_transition_is_rejected() -> None:
    with pytest.raises(ValueError, match="illegal promotion transition"):
        validate_stage_transition(
            PromotionStage.DRAFT,
            PromotionStage.PRODUCTION,
            ActorKind.HUMAN_APPROVER,
        )


def test_approval_window_is_explicit() -> None:
    record = approval()
    assert record.is_active(datetime(2026, 8, 4, 12, tzinfo=UTC))
    assert not record.is_active(datetime(2026, 8, 6, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        record.is_active(datetime(2026, 8, 4))


def test_approval_rejects_invalid_expiry() -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    with pytest.raises(ValidationError, match="expiry"):
        approval(approved_at=now, expires_at=now)


def test_approval_hash_is_deterministic() -> None:
    assert approval().sha256() == approval().sha256()
