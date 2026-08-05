"""Canonical, evidence-only operator ledger for completed legacy cleanup."""

from __future__ import annotations

from pathlib import Path
from time import time_ns

from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.retirement.approval import RetirementApprovalPaths
from aiquanttrader.retirement.cleanup import MAX_CONTROL_BYTES, _read_regular
from aiquanttrader.retirement.models import (
    CleanupActionPlan,
    CleanupCompletionReport,
    CleanupOperatorLedger,
    CleanupOperatorLedgerEntry,
    CleanupPreflightReceipt,
    DisabledObservationReport,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    RetirementPolicy,
)
from aiquanttrader.retirement.outcome import _replay_cleanup_completion


def assemble_cleanup_closeout(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    completion_report: CleanupCompletionReport,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_action_plan: CleanupActionPlan,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupOperatorLedger:
    """Build a closeout ledger by replaying all retained cleanup evidence."""

    return _build_cleanup_closeout(
        outcome_evidence_root,
        approved_evidence_root,
        action_evidence_root,
        completion_report,
        preflight_receipt,
        cleanup_action_plan,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        generated_ts_ns=time_ns(),
    )


def verify_cleanup_closeout(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    ledger: CleanupOperatorLedger,
    completion_report: CleanupCompletionReport,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_action_plan: CleanupActionPlan,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupOperatorLedger:
    """Independently replay a retained ledger at its original timestamp."""

    if ledger.generated_ts_ns > time_ns():
        raise ValueError("cleanup operator ledger is dated after verification")
    replayed = _build_cleanup_closeout(
        outcome_evidence_root,
        approved_evidence_root,
        action_evidence_root,
        completion_report,
        preflight_receipt,
        cleanup_action_plan,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        generated_ts_ns=ledger.generated_ts_ns,
    )
    if replayed != ledger:
        raise ValueError("cleanup operator ledger does not match source replay")
    return replayed


def load_cleanup_operator_ledger(path: Path) -> CleanupOperatorLedger:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    ledger = CleanupOperatorLedger.model_validate_json(payload)
    if payload != ledger.canonical_bytes() + b"\n":
        raise ValueError("cleanup operator ledger is not canonical JSON")
    return ledger


def _build_cleanup_closeout(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    completion_report: CleanupCompletionReport,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_action_plan: CleanupActionPlan,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
    generated_ts_ns: int,
) -> CleanupOperatorLedger:
    if generated_ts_ns < completion_report.generated_ts_ns:
        raise ValueError("cleanup operator ledger predates the completion report")
    if (
        archive_manifest.retention_expires_ts_ns - generated_ts_ns
        < policy.minimum_archive_retention_ns
    ):
        raise ValueError("cleanup closeout lacks required remaining archive retention")

    replay = _replay_cleanup_completion(
        outcome_evidence_root,
        approved_evidence_root,
        action_evidence_root,
        preflight_receipt,
        cleanup_action_plan,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        generated_ts_ns=completion_report.generated_ts_ns,
    )
    if replay.report != completion_report or not completion_report.cleanup_complete:
        raise ValueError("cleanup completion report does not match complete source replay")

    entries = tuple(
        CleanupOperatorLedgerEntry(
            sequence=step.sequence,
            plan_step_id=step.step_id,
            stage=step.stage,
            target_id=step.target_id,
            kind=step.kind,
            locator=step.locator,
            action=step.action,
            destination_locator=step.destination_locator,
            expected_state_sha256=step.expected_state_sha256,
            required_outcome=step.required_outcome,
            evidence_requirements=step.evidence_requirements,
            action_started_ts_ns=outcome.action_started_ts_ns,
            action_completed_ts_ns=outcome.action_completed_ts_ns,
            target_outcome_evidence_sha256=outcome.sha256(),
            postcondition_sha256=outcome.postcondition_sha256(),
            collected_by=outcome.collected_by,
            reviewed_by=outcome.reviewed_by,
            status="verified_complete",
        )
        for step, outcome in zip(replay.action_plan.steps, replay.outcome.targets, strict=True)
    )
    payload = {
        "schema_version": 1,
        "retirement_id": cleanup_manifest.retirement_id,
        "generated_ts_ns": generated_ts_ns,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "source_commit_sha": cleanup_manifest.source_commit_sha,
        "archive_manifest_sha256": archive_manifest.sha256(),
        "disabled_observation_report_sha256": disabled_report.sha256(),
        "cleanup_manifest_sha256": cleanup_manifest.sha256(),
        "cleanup_preflight_receipt_sha256": replay.preflight.sha256(),
        "cleanup_action_plan_sha256": replay.action_plan.sha256(),
        "cleanup_approval_sha256": replay.preflight.cleanup_approval_sha256,
        "approval_verification_id": replay.preflight.approval_verification_id,
        "cleanup_completion_report_sha256": completion_report.sha256(),
        "outcome_evidence_manifest_sha256": replay.outcome.manifest.sha256(),
        "outcome_evidence_bundle_sha256": replay.outcome.bundle_sha256,
        "credential_scan_sha256": replay.outcome.credential_scan.sha256(),
        "native_deployment_id": disabled_report.native_deployment_id,
        "native_admission_id": disabled_report.native_admission_id,
        "verification_mode": "evidence_only",
        "operator_actions_observed": True,
        "entries": [item.model_dump(mode="json") for item in entries],
    }
    return CleanupOperatorLedger.model_validate(
        {**payload, "ledger_id": canonical_sha256(payload), "closeout_complete": True}
    )
