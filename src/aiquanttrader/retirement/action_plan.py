"""Deterministic, evidence-only planning for separately authorized cleanup."""

from __future__ import annotations

from pathlib import Path
from time import time_ns

from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.retirement.approval import RetirementApprovalPaths
from aiquanttrader.retirement.cleanup import MAX_CONTROL_BYTES, _read_regular
from aiquanttrader.retirement.models import (
    CleanupActionPlan,
    CleanupActionPlanStep,
    CleanupActionStage,
    CleanupPreflightReceipt,
    DisabledObservationReport,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    LegacyCleanupTarget,
    RetirementPolicy,
    cleanup_action_evidence_for,
    cleanup_action_outcome_for,
    cleanup_action_stage_for,
)
from aiquanttrader.retirement.preflight import verify_cleanup_preflight


def prepare_cleanup_action_plan(
    approved_evidence_root: Path,
    action_evidence_root: Path,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupActionPlan:
    """Prepare a manual plan only while the complete preflight remains active."""

    receipt = verify_cleanup_preflight(
        approved_evidence_root,
        action_evidence_root,
        preflight_receipt,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
    )
    prepared_ts_ns = time_ns()
    if prepared_ts_ns >= receipt.valid_until_ts_ns:
        raise ValueError("cleanup preflight expired while preparing the action plan")
    return _build_cleanup_action_plan(
        receipt,
        cleanup_manifest,
        prepared_ts_ns=prepared_ts_ns,
    )


def verify_cleanup_action_plan(
    approved_evidence_root: Path,
    action_evidence_root: Path,
    action_plan: CleanupActionPlan,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupActionPlan:
    """Replay an action plan without refreshing its preflight validity window."""

    verified_ts_ns = time_ns()
    if action_plan.prepared_ts_ns > verified_ts_ns:
        raise ValueError("cleanup action plan is dated after verification")
    if verified_ts_ns >= action_plan.valid_until_ts_ns:
        raise ValueError("cleanup action plan has expired")
    receipt = verify_cleanup_preflight(
        approved_evidence_root,
        action_evidence_root,
        preflight_receipt,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
    )
    replayed = _build_cleanup_action_plan(
        receipt,
        cleanup_manifest,
        prepared_ts_ns=action_plan.prepared_ts_ns,
    )
    if replayed != action_plan:
        raise ValueError("cleanup action plan does not match source replay")
    return replayed


def load_cleanup_action_plan(path: Path) -> CleanupActionPlan:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    plan = CleanupActionPlan.model_validate_json(payload)
    if payload != plan.canonical_bytes() + b"\n":
        raise ValueError("cleanup action plan is not canonical JSON")
    return plan


def _build_cleanup_action_plan(
    receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    *,
    prepared_ts_ns: int,
) -> CleanupActionPlan:
    if not receipt.ready_for_operator_action:
        raise ValueError("cleanup preflight does not permit an action plan")
    if prepared_ts_ns < receipt.evaluated_ts_ns or prepared_ts_ns >= receipt.valid_until_ts_ns:
        raise ValueError("cleanup action plan timestamp is outside the preflight window")
    if receipt.approved_cleanup_manifest_sha256 != cleanup_manifest.sha256():
        raise ValueError("cleanup action plan manifest differs from preflight authority")

    approved_targets = {
        (
            item.target_id,
            item.kind,
            item.locator,
            item.action,
            item.destination_locator,
            item.expected_state_sha256,
        )
        for item in cleanup_manifest.targets
    }
    preflight_targets = {
        (
            item.target_id,
            item.kind,
            item.locator,
            item.action,
            item.destination_locator,
            item.expected_state_sha256,
        )
        for item in receipt.targets
        if item.state_matches and item.expected_state_sha256 == item.observed_state_sha256
    }
    if approved_targets != preflight_targets:
        raise ValueError("cleanup action plan targets differ from the verified preflight")

    ordered_targets = sorted(
        cleanup_manifest.targets,
        key=lambda item: (
            _stage_rank(item),
            item.kind.value,
            item.locator,
            item.target_id,
        ),
    )
    steps = tuple(
        _build_step(sequence, target) for sequence, target in enumerate(ordered_targets, start=1)
    )
    payload = {
        "schema_version": 1,
        "retirement_id": receipt.retirement_id,
        "prepared_ts_ns": prepared_ts_ns,
        "preflight_evaluated_ts_ns": receipt.evaluated_ts_ns,
        "valid_until_ts_ns": receipt.valid_until_ts_ns,
        "policy_id": receipt.policy_id,
        "policy_sha256": receipt.policy_sha256,
        "disabled_observation_report_sha256": receipt.disabled_observation_report_sha256,
        "archive_manifest_sha256": receipt.archive_manifest_sha256,
        "approved_cleanup_manifest_sha256": cleanup_manifest.sha256(),
        "preflight_receipt_sha256": receipt.sha256(),
        "cleanup_approval_sha256": receipt.cleanup_approval_sha256,
        "approval_verification_id": receipt.approval_verification_id,
        "native_deployment_id": receipt.native_deployment_id,
        "native_admission_id": receipt.native_admission_id,
        "source_commit_sha": receipt.source_commit_sha,
        "final_tag_name": receipt.final_tag_name,
        "execution_mode": "evidence_only",
        "commands_included": False,
        "operator_action_required": True,
        "operator_ledger_required": True,
        "steps": [item.model_dump(mode="json") for item in steps],
        "ready_for_manual_action": True,
    }
    return CleanupActionPlan.model_validate(
        {
            **payload,
            "plan_id": canonical_sha256(payload),
        }
    )


def _stage_rank(target: LegacyCleanupTarget) -> int:
    stage_order = {stage: rank for rank, stage in enumerate(CleanupActionStage, start=1)}
    return stage_order[cleanup_action_stage_for(target)]


def _build_step(sequence: int, target: LegacyCleanupTarget) -> CleanupActionPlanStep:
    payload = {
        "sequence": sequence,
        "stage": cleanup_action_stage_for(target),
        "target_id": target.target_id,
        "kind": target.kind,
        "locator": target.locator,
        "action": target.action,
        "destination_locator": target.destination_locator,
        "expected_state_sha256": target.expected_state_sha256,
        "required_outcome": cleanup_action_outcome_for(target),
        "evidence_requirements": cleanup_action_evidence_for(target),
        "manual_action_required": True,
    }
    return CleanupActionPlanStep.model_validate(
        {
            **payload,
            "step_id": canonical_sha256(payload),
        }
    )
